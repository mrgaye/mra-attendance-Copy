from getpass import getpass
from auth import create_gm

print("\nMRA STAFF ATTENDANCE")
print("Initial General Manager Setup\n")

name = input("Full name: ")
phone = input("Phone number (+220): ")
pin = getpass("Create a 6-digit PIN: ")
confirm = getpass("Confirm PIN: ")

if pin != confirm:
    print("PINs do not match. Please try again.")
else:
    try:
        create_gm(name, phone, pin)
        print("\nGeneral Manager account created successfully!")
    except ValueError as error:
        print("\nSetup failed:", error)
