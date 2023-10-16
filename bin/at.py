import os
import yaml
from yaml.loader import SafeLoader

print("Hello world!")
print(os.environ['groups'])
print(os.environ['credentials'])

filePath = os.environ['credentials']
# Open the file and load the file
with open(filePath) as f:
    data = yaml.load(f, Loader=SafeLoader)
    print(data)
    print(data['credentials']['aws'])
