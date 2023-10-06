import os
import yaml

print("Hello world!")
print(os.environ['credentials'])

with open(os.environ['credentials']) as f:
    contents = f.readlines()
    print(yaml.dump(contents))