#!/usr/bin/env python3

try:
  with open('/proc/self/comm', 'r+') as f:
    f.write('ptr-ollama')
except (FileNotFoundError, PermissionError):
  pass

import sys, os, time, urllib.request, urllib.error, json, subprocess

deadline = time.time() + (3*60*60)

ollama = subprocess.Popen(('ollama', 'serve'))

completed = 0
while time.time() < deadline:
  req = urllib.request.Request('http://localhost:11434/api/generate')
  with open('ollama.json', 'r') as f:
    data = json.load(f)
  data.setdefault('options', {}).setdefault('seed',
                                            int.from_bytes(os.urandom(4)))
  if data.get('abort'):
    break
  for k in tuple(data.keys()):
    if k.startswith('#'):
      data.pop(k)
  target = data.pop('target', 3)
  data = json.dumps(data, indent = 2).encode()
  req.add_header('Content-Length', len(data))
  try:
    resp = urllib.request.urlopen(req, data = data)
  except urllib.error.URLError:
    time.sleep(3)
    continue
  with open('ollama_buf.txt', 'ab', buffering = 0) as fh:
    fh.write(time.strftime('\n\n\n\n%c\n').encode() + data + b'\n\n')
    for j in resp:
      js = json.loads(j)
      fh.write(js['response'].encode())
  completed += 1
  if completed == target:
    break
