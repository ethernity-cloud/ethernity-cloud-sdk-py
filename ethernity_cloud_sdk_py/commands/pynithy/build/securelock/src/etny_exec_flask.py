# etny_exec.py

import os
import sys
import ast
import json
import threading
import time
import secrets
import socket
import logging
from flask import Flask, request, jsonify
from functools import wraps
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pyngrok import ngrok
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import importlib

# ===========================
# Backend Functions Loading
# ===========================

try:
    import serverless.backend as backend
except ImportError:
    backend = None
    pass

sdkFunctions = {}
if backend is not None:
    for func in backend.__dict__.keys():
        if func not in backend.__builtins__.keys() and func not in [
            "__file__",
            "__cached__",
            "__builtins__",
        ]:
            sdkFunctions.update({func: backend.__dict__[func]})

server_instance = None

# ===========================
# Flask and ngrok Setup
# ===========================

class EthernityEnclaveServer:

    # Initialize the persistent execution context
    globals_context = {}

    # Initialize a reentrant threading lock for thread-safe access to the execution context
    execution_lock = threading.RLock()

    def __init__(self, local_port=5000):
        self.api_key = self.generate_api_key()
        self.local_port = local_port
        self.ngrok_url = None
        self.app = Flask(__name__)
        self.setup_security()
        self.setup_execution_environment()
        self.setup_routes()
        self.server_thread = threading.Thread(target=self.run_flask, daemon=True)
        self.ngrok_thread = threading.Thread(target=self.start_ngrok, daemon=True)

    def ___etny_result___(self, data):
        return([0, data])

    def generate_api_key(self):
        """Generates a secure random API key."""
        return secrets.token_hex(16)  # Generates a 32-character hexadecimal string

    def setup_security(self):
        """Sets up authentication and rate limiting."""
        # Authentication Decorator
        def require_api_key(f):
            @wraps(f)
            def decorated(*args, **kwargs):
                key = request.headers.get('x-api-key')
                if not key or key != self.api_key:
                    return jsonify({"status": "error", "message": "Unauthorized: Invalid or missing API key."}), 401
                return f(*args, **kwargs)
            return decorated

        # Rate Limiting using Flask-Limiter
        self.limiter = Limiter(
            key_func=get_remote_address,
            default_limits=["200 per day", "50 per hour"]
        )

        self.limiter.init_app(self.app)

        # Attach the authentication decorator to the server instance
        self.require_api_key = require_api_key

    def setup_execution_environment(self):
        """Initializes the secure execution environment."""
        # Initialize the persistent execution context
        class GlobalContext:
            def __init__(self):
                self.context = {}
                self.lock = threading.RLock()

            def set(self, name, value, timeout=5):
                acquired = self.lock.acquire(timeout=timeout)
                if not acquired:
                    raise RuntimeError(f"Failed to acquire lock to set '{name}'.")
                try:
                    self.context[name] = value
                finally:
                    self.lock.release()

            def get(self, name, timeout=5):
                acquired = self.lock.acquire(timeout=timeout)
                if not acquired:
                    raise RuntimeError(f"Failed to acquire lock to get '{name}'.")
                try:
                    value = self.context.get(name)
                    return value
                finally:
                    self.lock.release()

            def update(self, updates, timeout=5):
                acquired = self.lock.acquire(timeout=timeout)
                if not acquired:
                    raise RuntimeError("Failed to acquire lock to update global context.")
                try:
                    self.context.update(updates)
                finally:
                    self.lock.release()

        self.globals_context = GlobalContext()

        # Define set_global and get_global functions
        def set_global(name, value):
            """
            Sets a global variable in globals_context.
            Args:
                name (str): The name of the global variable.
                value: The value to assign to the global variable.
            """
            try:
                self.globals_context.set(name, value)
            except RuntimeError as e:
                print("Error setting global variable")
                # Depending on design, you might want to propagate the exception or handle it here

        def get_global(name):
            """
            Retrieves a global variable from globals_context.
            Args:
                name (str): The name of the global variable.
            Returns:
                The value of the global variable if it exists, else None.
            """
            try:
                return self.globals_context.get(name)
            except RuntimeError as e:
                print("Error getting global variable")
                return None

        self.set_global = set_global
        self.get_global = get_global

        # Load backend functions
        self.load_backend_functions()

    def load_backend_functions(self):
        """Dynamically load or reload backend functions from the 'serverless.backend' module."""
        try:
            import serverless.backend as backend
            importlib.reload(backend)  # Reload to get updated functions

            # Inject set_global and get_global into the backend module's namespace
            backend.set_global = self.set_global
            backend.get_global = self.get_global

            functions = {
                func: getattr(backend, func)
                for func in dir(backend)
                if callable(getattr(backend, func)) and not func.startswith("__")
            }

            # Update the global context with backend functions
            self.globals_context.update(functions)
        except ImportError:
            print("backend module not found")
        except Exception as e:
            print("error loading backend functions")

    def setup_routes(self):
        """Defines Flask routes."""
        @self.app.route('/execute', methods=['POST'])
        @self.require_api_key
        @self.limiter.limit("10 per minute")  # Additional rate limit for this endpoint
        def execute():
            """
            Executes user-provided Python code and returns the outputs.
            """
            data = request.get_json()
            if not data:
                return jsonify({"status": "error", "message": "Invalid JSON payload."}), 400

            payload = data.get('payload')
            input_data = data.get('input_data', None)

            if not payload:
                return jsonify({"status": "error", "message": "No payload provided."}), 400

            # Execute the payload
            result = self.Exec(payload, input_data)

            return jsonify(result)

        @self.app.route('/reload_backend', methods=['POST'])
        @self.require_api_key
        def reload_backend():
            """
            Reloads backend functions dynamically.
            """
            try:
                self.load_backend_functions()
                return jsonify({"status": "success", "message": "Backend functions reloaded successfully."}), 200
            except Exception as e:
                return jsonify({"status": "error", "message": "Failed to reload backend functions."}), 500

        @self.app.route('/health', methods=['GET'])
        def health():
            """Health check endpoint."""
            return jsonify({"status": "healthy"}), 200

        @self.app.route('/list_functions', methods=['GET'])
        @self.require_api_key
        def list_functions():
            """
            Lists all loaded backend functions.
            """
            functions = {name: func.__doc__ for name, func in self.globals_context.context.items() if callable(func)}
            return jsonify({"status": "success", "functions": functions}), 200

    def run_flask(self):
        """Runs the Flask server."""
        self.app.run(port=self.local_port, debug=False, use_reloader=False)

    def start_ngrok(self):
        """Starts ngrok tunnel to forward the specified port."""

        print("Starting ngrok")
        try:
            # Authenticate with ngrok
            print("Setting authentication token")
            auth_token = os.environ.get("ETNY_NGROK_AUTHTOKEN", "2orw7I8y15uYu4ajtlsGwimgAqK_6ke12fFHvBdkWkyYpEyf6")
            print(auth_token)
            ngrok.set_auth_token(auth_token)
            print(ngrok)
            # Forward the port with TLS
            public_url = ngrok.connect(self.local_port, bind_tls=True)

            print(public_url)
            self.ngrok_url = public_url.public_url
            print(f"ngrok tunnel established at {self.ngrok_url}")
        except Exception as e:
            print("Failed to start ngrok tunnel")

    def initialize_application(self, payload_data, input_data = None):
        """
        Initializes the EthernityEnclaveServer and executes the initial deployment function.
        """

        health_url = f"{self.ngrok_url}/health"
        headers = {"x-api-key": self.api_key}

        for attempt in range(5):
            try:
                health_response = requests.get(health_url, headers=headers)
                if health_response.status_code == 200 and health_response.json().get("status") == "healthy":
                    break
                else:
                   print(f"Health check failed (Attempt {attempt + 1}/5). Retrying...")
            except Exception as e:
                print(f"Health check error (Attempt {attempt + 1}/5): {e}")

            time.sleep(1)

        if health_response.status_code != 200:
            return

        initial_deployment_payload = payload_data  # Adjust as per your deploy function

        # Step 5: Prepare the request headers
        exec_headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key
        }

        # Step 6: Prepare the JSON data
        exec_data = {
            "payload": initial_deployment_payload,
            "input_data": None
        }

        # Step 7: Send the POST request to /execute
        execute_url = f"{self.ngrok_url}/execute"

        print("Executing initial deployment...")
        try:
            exec_response = requests.post(execute_url, headers=exec_headers, json=exec_data)
            if exec_response.status_code == 200:
                exec_result = exec_response.json()
            else:
                print("Response:", exec_response.json())
        except Exception as e:
            print(f"Error executing initial deployment: {e}")


    def start(self, payload_data, input_data=None):
        """Starts the Flask server and ngrok tunnel."""
        self.server_thread.start()
        self.ngrok_thread.start()

        while(self.ngrok_url == None):
            time.sleep(0.1)

        self.initialize_application(payload_data, input_data)


    def Exec(self, payload_data, input_data=None):
        """
        Executes the provided payload within the persistent execution context.
        """
        try:
            if payload_data:
                local_context = {}
                if input_data is not None:
                    self.globals_context.set("___etny_data_set___", input_data)
                module = ast.parse(payload_data)
                outputs = []
                for node in module.body:
                    if isinstance(node, ast.Expr):
                        #print("node:", ast.unparse(node.value))
                        #print("ctx:", self.globals_context.context)
                        result = self.secure_exec_with_timeout("enty_expr_result = " + ast.unparse(node.value), globals_ctx=self.globals_context, locals_ctx=local_context, timeout=120)

                        #print("ctxa:", self.globals_context.context)
                        #print("output=", result['outputs']['enty_expr_result'])

                        outputs.append(result['outputs']['enty_expr_result'])
                    else:
                        # Handle statements if needed
                        #print("ctx:", self.globals_context.context)
                        self.secure_exec_with_timeout(ast.Module([node], type_ignores=[]), globals_ctx=self.globals_context, locals_ctx=local_context, timeout=120)
                        #print("ctxa:", self.globals_context.context)

            result_object = {"outputs": outputs}
            return self.___etny_result___(json.dumps(result_object, separators=(",", ":")))

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def is_json_serializable(self, obj):
        """Check if the object is JSON serializable."""
        try:
            json.dumps(obj)
            return True
        except (TypeError, OverflowError):
            return False

    def secure_exec(self, code, globals_ctx=None, locals_ctx=None):
        """
        Executes user-provided code in a restricted environment.
        Only JSON-serializable outputs are returned.
        Exposes set_global and get_global for dynamic global variable management.
        """
        try:
            byte_code = compile_restricted(code, '<string>', 'exec')


            exec(byte_code, {
                '__builtins__': safe_builtins,
                'set_global': self.set_global,  # Expose set_global
                'get_global': self.get_global,  # Expose get_global
                **globals_ctx.context
            }, locals_ctx)

            excluded_vars = {'___etny_data_set___', '___etny_result___'}

            outputs = {}
            for var, value in locals_ctx.items():
                if var not in excluded_vars and not var.startswith('__'):
                    if self.is_json_serializable(value):
                        outputs[var] = value
                    else:
                        outputs[var] = str(value)

            return {"status": "success", "outputs": outputs}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def secure_eval(self, code, globals_ctx=None, locals_ctx=None):
        """
        Executes user-provided code in a restricted environment.
        Only JSON-serializable outputs are returned.
        Exposes set_global and get_global for dynamic global variable management.
        """
        try:
            print("eval:", code)

            byte_code = compile_restricted(code, '<inline code>', 'eval')

            result = eval(byte_code, {
                '__builtins__': safe_builtins,
                'return': self.set_global,
                'set_global': self.set_global,  # Expose set_global
                'get_global': self.get_global,  # Expose get_global
                **globals_ctx.context
            }, locals_ctx)
            print("res:", result)

            excluded_vars = {'___etny_data_set___', '___etny_result___'}

            outputs.append(result)

            return {"status": "success", "outputs": outputs}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    def secure_exec_with_timeout(self, code, globals_ctx=None, locals_ctx=None, timeout=15):
        """
        Executes user-provided code in a restricted environment with a timeout.
        Only JSON-serializable outputs are returned.
        """
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.secure_exec, code, globals_ctx, locals_ctx)
            try:
                result = future.result(timeout=timeout)
                return result
            except TimeoutError:
                return {"status": "error", "message": "Code execution timed out."}
            except Exception as e:
                return {"status": "error", "message": str(e)}

    def secure_eval_with_timeout(self, code, globals_ctx=None, locals_ctx=None, timeout=15):
        """
        Executes user-provided code in a restricted environment with a timeout.
        Only JSON-serializable outputs are returned.
        """
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.secure_eval, code, globals_ctx, locals_ctx)
            try:
                result = future.result(timeout=timeout)
                print("rt:", result)
                return result
            except TimeoutError:
                return {"status": "error", "message": "Code execution timed out."}
            except Exception as e:
                return {"status": "error", "message": str(e)}


    def extract_variables_from_ast(self, payload_data):
        """Extracts variable names assigned in the payload."""
        try:
            parsed = ast.parse(payload_data)
            variables = set()
            for node in ast.walk(parsed):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            variables.add(target.id)
                elif isinstance(node, (ast.Tuple, ast.List)):
                    for elt in node.elts:
                        if isinstance(elt, ast.Name):
                            variables.add(elt.id)
            return variables
        except Exception as e:
            return set()

def execute_server_v4(payload_data, input_data=None, globals=None, locals=None):
    """
    The main ExecServer function that sets up the server.
    It does not execute any tasks directly.

    Args:
        payload_data (str): The Python code to execute (unused in this setup).
        input_data (any): Optional input data (unused in this setup).
        globals (dict): Global variables.
        locals (dict): Local variables.

    Returns:
        dict: Information about the server setup.
    """
    global server_instance
    server_lock = threading.Lock()
    with server_lock:
        print("SL")
        if server_instance is None:
            # Initialize and start the EthernityEnclaveServer
            print("SI")
            server_instance = EthernityEnclaveServer(local_port=5000)
            server_instance.start(payload_data, input_data)
            # Wait briefly to ensure ngrok tunnel is established
            time.sleep(2)
            if not server_instance.ngrok_url:
                return {
                    "status": "error",
                    "message": "Failed to establish ngrok tunnel."
                }

    # Return server details
    return [ 0, f"{server_instance.ngrok_url}:{server_instance.api_key}"]

# ===========================
# Server Management
# ===========================

def execute_task_v4(payload_data, input_data=None):
    """
    Executes the task by calling the ExecFunction.

    Args:
        payload_data (str): The Python code to execute.
        input_data (any): Optional input data.

    Returns:
        dict: Result of the execution.
    """

    global server_instance
    if server_instance is None:
        # Initialize and start the EthernityEnclaveServer
        server_instance = EthernityEnclaveServer(local_port=5000)

    return server_instance.Exec(
        payload_data,
        input_data,
    )
