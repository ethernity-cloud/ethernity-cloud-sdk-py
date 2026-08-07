import json

# Default (disabled) Enclave State Registry block. Additive and optional: a
# .config.json without an "ESR" key behaves exactly as before ESR existed.
ESR_DEFAULTS = {
    "enabled": False,
    "contract_address": "",
    "wallet_address": "",
    # Auto-funding is an OPT-IN CONVENIENCE, never a default. Manual funding by
    # the data owner is the documented path: the payload uses the ESR address
    # for its own purposes, and only the data owner knows what it needs. With
    # this off, publish merely PRINTS the enclave's address and never moves
    # value -- so publishing can never transfer funds as a side effect.
    "autofund": {
        "enabled": False,
        "amount": "",
        "threshold": "",
        "max": "",
    },
}


class Config:
    def __init__(self, file_path):
        """
        Initialize the Config class with the path to the configuration file.

        Args:
            file_path (str): Path to the JSON configuration file.
        """
        self.file_path = file_path
        self.config = {}

    def load(self):
        """
        Load the JSON configuration file into the class's dictionary.

        Returns:
            dict: The loaded configuration dictionary.
        """
        try:
            with open(self.file_path, 'r') as f:
                self.config = json.load(f)
            if not isinstance(self.config, dict):
                raise ValueError("JSON file must contain a dictionary.")
        except FileNotFoundError:
            print(f"File not found: {self.file_path}. Initializing with an empty configuration.")
            self.config = {}
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON file: {e}. Initializing with an empty configuration.")
            self.config = {}
        return self.config

    def read(self, variable):
        """
        Read the value of a specific variable from the configuration.

        Args:
            variable (str): The variable key to retrieve.

        Returns:
            Any: The value associated with the variable, or None if not found.
        """
        return self.config.get(variable, None)

    def write(self, variable, value):
        """
        Write or update a variable in the configuration and save it to the file.

        Args:
            variable (str): The variable key to write or update.
            value (Any): The value to set for the variable.

        Returns:
            dict: The updated configuration dictionary.
        """
        self.config[variable] = value
        try:
            with open(self.file_path, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"Error writing to file: {e}")
        return self.config

    def read_esr(self):
        """Typed ESR block, stored defaults merged over ESR_DEFAULTS.

        Always returns a complete dict; an absent/partial "ESR" key is filled
        from ESR_DEFAULTS so callers never need existence checks.
        """
        stored = self.config.get("ESR") or {}
        merged = json.loads(json.dumps(ESR_DEFAULTS))  # deep copy
        if isinstance(stored, dict):
            for k, v in stored.items():
                if k == "autofund" and isinstance(v, dict):
                    merged["autofund"].update(v)
                else:
                    merged[k] = v
        return merged

    def write_esr(self, esr):
        return self.write("ESR", esr)


config = None