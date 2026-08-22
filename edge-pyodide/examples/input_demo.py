"""input() round-trip: edgepy bridges the terminal, a pipe, or EOF into the sandbox."""
name = input("Your name: ")
print(f"Hi {name}!")
try:
    more = input("Anything else? ")
except EOFError:
    more = "(EOF)"
print("You said:", more)
