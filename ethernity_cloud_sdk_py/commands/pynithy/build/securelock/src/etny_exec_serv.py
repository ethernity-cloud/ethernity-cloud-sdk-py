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


# ===========================
# Task Status Definitions
# ===========================

class TaskStatus:
    SUCCESS = 0
    SYSTEM_ERROR = 1
    KEY_ERROR = 2
    SYNTAX_WARNING = 3
    BASE_EXCEPTION = 4
    PAYLOAD_NOT_DEFINED = 5
    PAYLOAD_CHECKSUM_ERROR = 6
    INPUT_CHECKSUM_ERROR = 7


# ===========================
# Flask and ngrok Setup
# ===========================

class EthernityEnclaveServer:
    def __init__(self, local_port=5000):
        self.api_key = self.generate_api_key()
        self.local_port = local_port
        self.ngrok_url = None
        self.app = Flask(__name__)
        self.setup_logging()
        self.setup_security()
        self.setup_execution_environment()
        self.setup_routes()
        self.server_thread = threading.Thread(target=self.run_flask, daemon=True)
        self.ngrok_thread = threading.Thread(target=self.start_ngrok, daemon=True)
    
    def generate_api_key(self):
        """Generates a secure random API key."""
        return secrets.token_hex(16)  # Generates a 32-character hexadecimal string
    
    def setup_logging(self):
        """Configures logging with rotation."""
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler('app.log', maxBytes=1000000, backupCount=5)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(handler)
        self.logger.info("Logging is set up.")
    
    def setup_security(self):
        """Sets up authentication and rate limiting."""
        # Authentication Decorator
        def require_api_key(f):
            @wraps(f)
            def decorated(*args, **kwargs):
                key = request.headers.get('x-api-key')
                if not key or key != self.api_key:
                    self.logger.warning("Unauthorized access attempt detected.")
                    return jsonify({"status": "error", "message": "Unauthorized: Invalid or missing API key."}), 401
                return f(*args, **kwargs)
            return decorated

        # Rate Limiting using Flask-Limiter
        self.limiter = Limiter(
            self.app,
            key_func=get_remote_address,
            default_limits=["200 per day", "50 per hour"]
        )
        self.logger.info("Rate limiting enabled: 200 requests/day and 50 requests/hour per IP.")
        self.require_api_key = require_api_key  # Attach to instance for use in routes
    
    def setup_execution_environment(self):
        """Initializes the secure execution environment."""
        self.globals_context = {
            "___etny_result___": self.___etny_result___,
            **sdkFunctions
        }
    
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
                self.logger.warning("No JSON payload received.")
                return jsonify({"status": "error", "message": "Invalid JSON payload."}), 400

            payload = data.get('payload')
            input_data = data.get('input_data', None)

            if not payload:
                self.logger.warning("No 'payload' provided in the request.")
                return jsonify({"status": "error", "message": "No payload provided."}), 400

            # Execute the payload
            result = execute_task(payload, input_data)

            return jsonify(result)
        
        @self.app.route('/reload_backend', methods=['POST'])
        @self.require_api_key
        def reload_backend():
            """
            Reloads backend functions dynamically.
            """
            self.logger.info("Reloading backend functions...")
            global backend, sdkFunctions
            try:
                importlib.reload(backend)  # Reload backend module
                sdkFunctions = {}
                if backend is not None:
                    for func in backend.__dict__.keys():
                        if func not in backend.__builtins__.keys() and func not in [
                            "__file__",
                            "__cached__",
                            "__builtins__",
                        ]:
                            sdkFunctions.update({func: backend.__dict__[func]})
                # Update globals_context with new sdkFunctions
                self.globals_context.update(sdkFunctions)
                self.logger.info("Backend functions reloaded successfully.")
                return jsonify({"status": "success", "message": "Backend functions reloaded successfully."}), 200
            except Exception as e:
                self.logger.error(f"Failed to reload backend functions: {e}", exc_info=True)
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
            functions = {name: func.__doc__ for name, func in sdkFunctions.items() if callable(func)}
            return jsonify({"status": "success", "functions": functions}), 200
    
    def run_flask(self):
        """Runs the Flask server."""
        self.app.run(port=self.local_port, debug=False, use_reloader=False)
    
    def start_ngrok(self):
        """Starts ngrok tunnel to forward the specified port."""
        try:
            self.logger.info("Starting ngrok tunnel...")
            # Authenticate with ngrok
            ngrok.set_auth_token(os.getenv("NGROK_AUTHTOKEN"))
            
            # Forward the port with TLS
            public_url = ngrok.connect(self.local_port, bind_tls=True)
            self.ngrok_url = public_url.public_url
            self.logger.info(f"ngrok tunnel established at {self.ngrok_url}")
            print(f"ngrok tunnel established at {self.ngrok_url}")
        except Exception as e:
            self.logger.error(f"Failed to start ngrok tunnel: {e}", exc_info=True)
    
    def start(self):
        """Starts the Flask server and ngrok tunnel."""
        self.server_thread.start()
        self.ngrok_thread.start()
        self.logger.info("Flask server and ngrok tunnel threads started.")
    
    # ===========================
    # Execution Functions
    # ===========================
    
    def ___etny_result___(self, data):
        """Handles the result of execution."""
        # Here, you can implement any post-execution handling if needed
        # For now, we'll simply return the data
        return data

def ___etny_result___(data):
    quit([0, data])


def execute_task(payload_data, input_data):
    return Exec(
        payload_data,
        input_data,
        {"___etny_result___": ___etny_result___, **sdkFunctions},
    )


def Exec(payload_data, input_data, globals=None, locals=None):
    try:
        if payload_data is not None:
            if input_data is not None:
                globals["___etny_data_set___"] = input_data
            module = ast.parse(payload_data)
            outputs = []
            for node in module.body:
                if isinstance(node, ast.Expr):
                    expr_code = compile(
                        ast.Expression(node.value), filename="<ast>", mode="eval"
                    )
                    result = eval(expr_code, globals, locals)
                    outputs.append(result)
                else:
                    # Handle statements if needed
                    exec(
                        compile(ast.Module([node], type_ignores=[]), filename="<ast>", mode="exec"),
                        globals,
                        locals,
                    )

            return ___etny_result___("\n".join(outputs))
        else:
            return (
                TaskStatus.PAYLOAD_NOT_DEFINED,
                "Could not find the source file to execute",
            )

        return TaskStatus.SUCCESS, "TASK EXECUTED SUCCESSFULLY"
    except SystemError as e:
        return TaskStatus.SYSTEM_ERROR, e.args[0]
    except KeyError as e:
        return TaskStatus.KEY_ERROR, e.args[0]
    except SyntaxWarning as e:
        return TaskStatus.SYNTAX_WARNING, e.args[0]
    except BaseException as e:
        try:
            if e.args[0][0] == 0:
                return TaskStatus.SUCCESS, e.args[0][1]
            else:
                return TaskStatus.BASE_EXCEPTION, e.args[0]
        except Exception as e:
            return TaskStatus.BASE_EXCEPTION, e.args[0]


# ===========================
# Main Execution
# ===========================

def main():
    # Initialize and start the EthernityEnclaveServer
    server = EthernityEnclaveServer(local_port=5000)
    server.start()

    # Wait briefly to ensure ngrok tunnel is established
    time.sleep(2)

    if not server.ngrok_url:
        print("Failed to establish ngrok tunnel.")
        sys.exit(1)

    print("EthernityEnclaveServer is running!")
    print(f"ngrok URL: {server.ngrok_url}")
    print(f"API Key: {server.api_key}")

    # Keep the main thread alive to continue listening for replies
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down the EthernityEnclaveServer.")
        # Disconnect all ngrok tunnels
        ngrok.kill()
        sys.exit()

if __name__ == "__main__":
    main()
