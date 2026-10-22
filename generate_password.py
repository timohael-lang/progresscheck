from getpass import getpass
from werkzeug.security import generate_password_hash

password = getpass("Придумай пароль администратора: ")

if len(password) < 12:
    print("Пароль должен содержать минимум 12 символов.")
else:
    print("\nADMIN_PASSWORD_HASH:")
    print(generate_password_hash(password))
