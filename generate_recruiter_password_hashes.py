
from argon2 import PasswordHasher
from getpass import getpass
from argon2.exceptions import HashingError

def generate_password_hash():
    """
    Generate Argon2 password hashes for recruiters.
    Copy the output into Streamlit secrets.toml.
    """
    ph = PasswordHasher()

    while True:
        username = input("Recruiter username (or 'quit'): ").strip()

        if username.lower() == "quit":
            break

        if not username:
            print("Username cannot be empty.\n")
            continue

        password = getpass(f"Password for {username}: ")

        if not password:
            print("Password cannot be empty.\n")
            continue

        try:
            password_hash = ph.hash(password)
        except HashingError as exc:
            print(f"Could not generate hash: {exc}\n")
            continue

        print(f"\n[{username}]")
        print(f'password_hash = "{password_hash}"\n')

    print("Finished generating password hashes.")


if __name__ == "__main__":
    print("Generate password hashes for recruiters.")
    print("Copy the output into your Streamlit secrets.toml file.")
    print("Do not commit credentials or hashes to a public repository.\n")

    generate_password_hash()
