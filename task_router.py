#!/usr/bin/env python3

import sys, os, tomllib

def main():
  try:
    phase = sys.argv[1] # e.g. pre_stop, task, post_restore
  except IndexError:
    phase = 'task'
  trusted_config_path = os.path.join(os.path.dirname(__file__),
                                     'task_router.toml')
  with open(trusted_config_path, 'rb') as f:
    trusted_config = tomllib.load(f)
  if home := trusted_config.get('home'):
    root = os.path.dirname(__file__)
    os.environ['HOME'] = os.path.abspath(os.path.join(root, home))
  untrusted_config_path = os.path.expanduser(
    trusted_config['untrusted_config_path']
  )
  if global_cwd := trusted_config.get('cwd'):
    global_cwd = os.path.expanduser(global_cwd)
    untrusted_config_path = os.path.join(global_cwd, untrusted_config_path)
  with open(untrusted_config_path, 'rb') as f:
    untrusted_config = tomllib.load(f)
  task_name = untrusted_config['task_name']
  cmd = trusted_config[task_name][phase]['cmd']
  if type(cmd) is not list:
    raise TypeError(f'{task_name}.{phase}.cmd is not a list')
  if not cmd:
    raise ValueError(f'{task_name}.{phase}.cmd is empty')
  if not os.path.isabs(cmd[0]):
    raise ValueError('Command executable must be an absolute path')
  if cwd := trusted_config[task_name][phase].get('cwd'):
    os.chdir(os.path.expanduser(cwd))
  elif global_cwd:
    os.chdir(global_cwd)
  if execv := getattr(os, 'execv', None):
    execv(cmd[0], cmd)
  else:
    import subprocess
    sys.exit(subprocess.run(cmd).returncode)

if __name__ == '__main__':
  main()
