import os
import json
import yaml

print("Hello world!")
print(os.environ['groups'])
print(os.environ['credentials'])

# f = open(os.environ['credentials'])
# jsonContent = json.load(f)
# print(jsonContent)
# print(jsonContent["cuenta"])

filePath = os.environ['credentials']
with open(filePath, "r") as stream:
    try:
        print(yaml.safe_load(stream))
        fileContent = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        print(exc)
print(fileContent['usuario'])
