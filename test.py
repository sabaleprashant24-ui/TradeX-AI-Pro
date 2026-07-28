import os

print("Current Folder:")
print(os.getcwd())

print("\nFiles:")
for f in os.listdir():
    print(f)