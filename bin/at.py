import os
import yaml

print("Hello world!")
print(os.environ['credentials'])

jsonContent = json.load(os.environ['credentials'])
f = open(os.environ['credentials'])
jsonContent = json.load(f)
print(jsonContent)
print(jsonContent["cuenta"])