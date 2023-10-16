import os
import json

print("Hello world!")
print(os.environ['groups'])
print(os.environ['credentials'])

f = open(os.environ['credentials'])
jsonContent = json.load(f)
print(jsonContent)
print(jsonContent["cuenta"])
