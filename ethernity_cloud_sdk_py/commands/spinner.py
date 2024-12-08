import sys
import time
import threading

class Spinner:
    SPINNER_FRAMES = [
        '⠋', '⠙', '⠹', '⠸', '⠼',
        '⠴', '⠦', '⠧', '⠇', '⠏'
    ]

    CHECK = "\033[92m\t\u2714\033[0m  "
    FAIL = "\033[91m\t\u2718\033[0m  "

    def __init__(self):
        pass

    def spin_message(self, message, stop_event):
        """Spin while waiting. Stop when stop_event is set."""
        frame_index = 0
        
        # Print initial state
        sys.stdout.write(f"\t{self.SPINNER_FRAMES[frame_index]}  {message}")
        sys.stdout.flush()
        
        while not stop_event.is_set():
            frame_index = (frame_index + 1) % len(self.SPINNER_FRAMES)
            sys.stdout.write("\r" + f"\t{self.SPINNER_FRAMES[frame_index]}  {message}")
            sys.stdout.flush()
            time.sleep(0.1)

    def show_checkmark(self, message):
        """Replace the spinner with a checkmark and move to a new line."""
        sys.stdout.write("\r" + f"{self.CHECK}{message}\n")
        sys.stdout.flush()

    def show_failed(self, message):
        """Replace the spinner with a fail mark and move to a new line."""
        sys.stdout.write("\r" + f"{self.FAIL}{message}\n")
        sys.stdout.flush()

    def spin_till_done(self, message, function, *args, **kwargs):
        result = None
        stop_event = threading.Event()
        spinner_thread = threading.Thread(target=self.spin_message, args=(message, stop_event), daemon=True)
        spinner_thread.start()

        try:
            result = function(*args, **kwargs)
        except KeyboardInterrupt:
            # User pressed Ctrl+C
            stop_event.set()
            spinner_thread.join()
            # You can optionally re-raise the KeyboardInterrupt to exit
            raise
        except Exception as e:
            stop_event.set()
            spinner_thread.join()
            self.show_failed(message)
            print(f"Exception: {e}")
            return result
        else:
            # No exception, function completed
            stop_event.set()
            spinner_thread.join()
            if result:
                self.show_checkmark(message)
            else:
                self.show_failed(message)
            return result