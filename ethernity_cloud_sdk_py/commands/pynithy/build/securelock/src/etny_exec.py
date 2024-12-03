import os.path
import ast

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

# Utility to extract variable names from the AST
def extract_variables_from_ast(payload_data):
    """Extracts only variable names assigned in the payload."""
    try:
        parsed = ast.parse(payload_data)
        variables = set()
        for node in ast.walk(parsed):
            if isinstance(node, ast.Assign):  # Capture assignment targets
                for target in node.targets:
                    if isinstance(target, ast.Name):  # Ensure it's a variable, not a function
                        variables.add(target.id)
            # Optionally handle list destructuring (e.g., `[a, b] = ...`)
            elif isinstance(node, ast.Tuple) or isinstance(node, ast.List):
                for elt in node.elts:
                    if isinstance(elt, ast.Name):
                        variables.add(elt.id)
        return variables
    except Exception as e:
        print(f"Error extracting variables: {str(e)}")
        return set()

def ___etny_result___(data):
    quit([0, data])


class TaskStatus:
    SUCCESS = 0
    SYSTEM_ERROR = 1
    KEY_ERROR = 2
    SYNTAX_WARNING = 3
    BASE_EXCEPTION = 4
    PAYLOAD_NOT_DEFINED = 5
    PAYLOAD_CHECKSUM_ERROR = 6
    INPUT_CHECKSUM_ERROR = 7


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
            variables = extract_variables_from_ast(payload_data)
            #print(f"Extracted variables: {variables}")
            for var in variables:
                if var not in globals:  # Ensure only valid types are in globals
                    globals[var] = None  # Initialize with default value

            #print(f"Payload Data:\n{payload_data}")

            # Parse the Python code
            try:
                #print("Parsing payload...")
                module = ast.parse(payload_data)
                #print("Parsing completed successfully.")
            except SyntaxError as e:
                error_details = {
                    "status": "error",
                    "message": f"Syntax error in payload: {e.msg}",
                    "details": {"lineno": e.lineno, "offset": e.offset},
                }
                return TaskStatus.SYNTAX_WARNING, error_details
            
            except Exception as e:
                print(f"Unexpected parsing error: {str(e)}")
                return TaskStatus.SYNTAX_WARNING, str(e)

            # Debugging AST
            print("AST Module Object:", module)

            outputs = []

            # Execute AST nodes
            for node in module.body:
                if isinstance(node, ast.Expr):  # Handle expressions
                    #print(f"Evaluating expression: {node}")
                    expr_code = compile(
                        ast.Expression(node.value), filename="<ast>", mode="eval"
                    )
                    result = eval(expr_code, globals, locals)
                    outputs.append(result)
                    #print(f"Expression Result: {result}")
                else:  # Handle other statements
                    #print(f"Executing statement: {node}")
                    module_with_node = ast.Module(body=[node], type_ignores=[])
                    exec(
                        compile(module_with_node, filename="<ast>", mode="exec"),
                        globals,
                        locals,
                    )
                    #print("Statement executed successfully.")

            return ___etny_result___("\n".join(outputs))
        else:
            return (
                TaskStatus.PAYLOAD_NOT_DEFINED,
                "Could not find the source file to execute",
            )

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


# payload_data = """hello('Iosif')
# hello('Luca')
# hello('World')"""
# print(execute_task(payload_data, None))
# print(execute_task(payload_data, None)[1])


# result = Exec('./v1/src/app/payload.py', './v1/src/app/input.txt',
#              {'etny_print': etny_print})
# print('task result:', result)

# write the task result in a file
# generate task hash
