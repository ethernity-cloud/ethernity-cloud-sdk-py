import hashlib
from eth_account import Account

def get_wallet_address(first_string, second_string):
    concat = (first_string + second_string).encode()
    _hash1 = hashlib.sha256(concat).hexdigest()
    _hash = hashlib.sha256(_hash1.encode()).hexdigest()
    acct = Account.from_key(_hash)
    return acct.address, _hash


if __name__ == '__main__':
    first_string = 'test'
    second_string = 'test'
    (addr, private) = get_wallet_address(first_string, second_string)
    print('account address = ', addr)  # 0xf63106856F7007A30025f0fFD5A534EA880878C3
    print('private = ', private)
