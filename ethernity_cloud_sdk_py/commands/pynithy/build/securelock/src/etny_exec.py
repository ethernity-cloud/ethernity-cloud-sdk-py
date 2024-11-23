import os.path

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
                return ___etny_result___(eval(payload_data, globals, locals))
            else:
                return ___etny_result___(eval(payload_data, globals, locals))
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


# result = Exec('./v1/src/app/payload.py', './v1/src/app/input.txt',
#              {'etny_print': etny_print})
# print('task result:', result)

# write the task result in a file
# generate task hash
