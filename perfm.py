#!/usr/bin/env python3

import sys, os, functools, tomllib

try:
  import fcntl
except ModuleNotFoundError:
  fcntl = None
  import msvcrt

DEFAULT_CONFIG = {
  'use_boot_time': True,
  'use_parent_processes': True,
}

class FileLock(object):
  def __init__(self, path = __file__):
    self.fh = open(path)

  def __enter__(self):
    if fcntl:
      fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
    else:
      msvcrt.locking(self.fh.fileno(), msvcrt.LK_LOCK, 1)

  def __exit__(self, *_):
    if fcntl:
      fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
    else:
      msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
    self.fh.close()

@functools.cache
def get_boot_time():
  import time
  try:
    with open('/proc/uptime', 'r') as f:
      return round(time.time() - float(f.read().split()[0]))
  except FileNotFoundError:
    pass

  import subprocess
  try:
    out = subprocess.check_output(('uptime')).decode()
    import re
    p  = r'\s*up\s*((?P<days>\d+) days?,?)?\s*'
    p += r'((?P<hours>\d+):(?P<minutes>\d+))?\s*'
    p += r'((?P<hr>\d+) hrs?,?)?\s*'
    p += r'((?P<min>\d+) mins?,?)?\s*'
    p += r'((?P<sec>\d+) secs?)?'
    m = re.search(p, out)
    if m:
      uptime = 0
      groups = m.groupdict(0)
      uptime += float(groups['days'])*(24*60*60)
      for i in ('hours', 'hr'):
        uptime += float(groups[i])*(60*60)
      for i in ('minutes', 'min'):
        uptime += float(groups[i])*60
      uptime += float(groups['sec'])
      return round((time.time() - uptime)/60)*60
  except FileNotFoundError:
    pass

  # TODO Windows

  try:
    return __import__('psutil').boot_time()
  except ModuleNotFoundError:
    return None

def process_exists(pid):
  if sys.platform == 'win32':
    import ctypes # TODO
  else:
    try:
      os.kill(pid, 0)
      return True
    except OSError:
      return False

@functools.cache
def get_config():
  config_dir = os.environ.get('XDG_CONFIG_DIR')
  if not config_dir:
    config_dir = os.environ.get('APPDATA')
  if not config_dir:
    config_dir = os.path.expanduser(os.path.join('~', '.config'))
  config = dict(DEFAULT_CONFIG)
  with open(os.path.join(config_dir, 'perfm.toml'), 'rb') as f:
    config.update(tomllib.load(f))
  return config

def C(name):
  return get_config().get(name)

def get_state_dir():
  state_dir = os.environ.get('XDG_STATE_HOME')
  if not state_dir:
    state_dir = os.environ.get('LOCALAPPDATA')
  if not state_dir:
    state_dir = os.path.expanduser(os.path.join('~', '.local', 'state'))
  return os.path.join(state_dir, 'perfm')

def load_state():
  try:
    with open(os.path.join(get_state_dir(), 'state.json'), 'r') as f:
      import json
      return json.load(f)
  except FileNotFoundError:
    return {}

def force_exit_internal(state):
  actions = C('actions')
  for actione_name in state.get('performed_actions', []):
    action = actions[actione_name]
    check_cmd = action.get('check_cmd')
    if check_cmd:
      import subprocess
      proc = subprocess.run(check_cmd, shell = True)
      should_perform = (proc.returncode != 0)
    else:
      should_perform = True
    if should_perform:
      exit_cmd = action.get('exit_cmd')
      if exit_cmd:
        import subprocess
        subprocess.run(exit_cmd, shell = True)
  state_dir = get_state_dir()
  try:
    os.remove(os.path.join(state_dir, 'state.json'))
  except FileNotFoundError:
    pass
  try:
    os.rmdir(state_dir)
  except OSError:
    pass

def load_clean_state():
  state = load_state()
  count = state.get('count', 0)
  if count > 0:
    if C('use_boot_time'):
      if ((boot_time := get_boot_time()) is not None and
          boot_time > state.get('boot_time', 0)):
        force_exit_internal(state)
        return {}
    if C('use_parent_processes'):
      ppids = set(state.get('ppids', []))
      for ppid in list(ppids):
        if not process_exists(ppid):
          ppids.pop(ppid)
          count -= 1
      if count == 0:
        force_exit_internal(state)
        return {}
  state['count'] = count
  return state

def enter():
  with FileLock():
    state = load_clean_state()
    count = state.get('count', 0)
    if count == 0:
      performed_actions = state.get('performed_actions', [])
      actions = C('actions')
      if not actions:
        raise KeyError('no actions configured')
      for action_name, action in actions.items():
        check_cmd = action.get('check_cmd')
        if check_cmd:
          proc = __import__('subprocess').run(check_cmd, shell = True)
          should_perform = (proc.returncode == 0)
        else:
          should_perform = True
        if should_perform:
          enter_cmd = action.get('enter_cmd')
          if enter_cmd:
            __import__('subprocess').run(enter_cmd, shell = True)
          performed_actions.append(action_name)
      state['performed_actions'] = performed_actions
      if C('use_boot_time'):
        state['boot_time'] = get_boot_time()
    if C('use_parent_processes'):
      state.setdefault('ppids', []).append(os.getppid())
    count += 1
    state['count'] = count
    state_dir = get_state_dir()
    os.makedirs(state_dir, exist_ok = True)
    with open(os.path.join(state_dir, 'state.json'), 'w') as f:
      import json
      json.dump(state, f)
    return state

def force_exit():
  with FileLock():
    state = load_state()
    force_exit_internal(state)
    return {}

def exit():
  with FileLock():
    state = load_clean_state()
    count = state.get('count')
    if count is None or count == 0:
      return {}
    if count == 1:
      force_exit_internal(state)
      return {}
    else:
      state['count'] = count - 1
      with open(os.path.join(get_state_dir(), 'state.json'), 'w') as f:
        import json
        json.dump(state, f)
      return state

def count():
  with FileLock():
    state = load_clean_state()
    print(state.get('count', 0))

def refresh_gui(members):
  with FileLock():
    state = load_clean_state()
    _refresh_gui(members, state)

def toggle_action(members, action):
  with FileLock():
    state = load_clean_state()
    performed_actions = state.get('performed_actions', [])
    performed = action in performed_actions
    config = get_config()
    if performed:
      exit_cmd = config.get('actions', []).get(action, {}).get('exit_cmd')
      if exit_cmd:
        import subprocess
        subprocess.run(exit_cmd, shell = True)
      performed_actions.remove(action)
    else:
      meta = config.get('actions', []).get(action)
      if meta:
        enter_cmd = meta.get('enter_cmd')
        if enter_cmd:
          import subprocess
          subprocess.run(enter_cmd, shell = True)
        performed_actions.append(action)
    if performed or meta:
      state['performed_actions'] = performed_actions
      state_dir = get_state_dir()
      os.makedirs(state_dir, exist_ok = True)
      with open(os.path.join(state_dir, 'state.json'), 'w') as f:
        import json
        json.dump(state, f)
    _refresh_gui(members, state)

def clear_layout(layout):
  if layout:
    while layout.count() > 0:
      item = layout.takeAt(0)
      widget = item.widget()
      if widget:
        widget.setParent(None)
      else:
        clear_layout(item.layout())

def _refresh_gui(members, state):
  import time, html, qtpy.QtWidgets

  count = state.get('count', 0)
  mode = 'Performance' if count > 0 else 'Normal'
  now = time.strftime('%c')

  status  = f'<b>Mode:</b> {mode}<br>'
  status += f'<b>Count:</b> {count}<br>'
  status += f'<b>Last Refresh:</b> {now}'
  members['status_lbl'].setText(status)

  action_layout = members['action_layout']
  clear_layout(action_layout)

  config = get_config()
  performed_actions = state.get('performed_actions', [])
  for action in config['actions']:
    performed = action in performed_actions
    mode = 'Performance' if performed else 'Normal'
    rt = f'{html.escape(action)} <i>{mode}</i>'
    alayout = qtpy.QtWidgets.QHBoxLayout()
    action_layout.addLayout(alayout)
    action_lbl = qtpy.QtWidgets.QLabel(rt)
    alayout.addWidget(action_lbl)
    toggle_btn = qtpy.QtWidgets.QPushButton(text = 'Toggle')
    alayout.addWidget(toggle_btn)
    toggle_btn.clicked.connect(lambda _, a=action: toggle_action(members, a))
  get_config.cache_clear()

def gui():
  import qtpy.QtWidgets
  app = qtpy.QtWidgets.QApplication(['perfm'])
  window = qtpy.QtWidgets.QWidget(parent = None)
  main_layout = qtpy.QtWidgets.QVBoxLayout()
  members = {'window': window}

  refresh_btn = qtpy.QtWidgets.QPushButton(text = "Refresh", parent = window)
  main_layout.addWidget(refresh_btn)
  refresh_btn.clicked.connect(lambda: refresh_gui(members))

  status_lbl = qtpy.QtWidgets.QLabel('', parent = window)
  main_layout.addWidget(status_lbl)
  members['status_lbl'] = status_lbl

  hlayout = qtpy.QtWidgets.QHBoxLayout()
  main_layout.addLayout(hlayout)

  enter_btn = qtpy.QtWidgets.QPushButton(text = "Enter", parent = window)
  hlayout.addWidget(enter_btn)
  enter_btn.clicked.connect(lambda: _refresh_gui(members, enter()))

  exit_btn = qtpy.QtWidgets.QPushButton(text = "Exit", parent = window)
  hlayout.addWidget(exit_btn)
  exit_btn.clicked.connect(lambda: _refresh_gui(members, exit()))

  force_exit_btn = qtpy.QtWidgets.QPushButton(text = "Force Exit",
                                              parent = window)
  hlayout.addWidget(force_exit_btn)
  force_exit_btn.clicked.connect(lambda: _refresh_gui(members, force_exit()))

  action_layout = qtpy.QtWidgets.QVBoxLayout()
  members['action_layout'] = action_layout

  scroll_widget = qtpy.QtWidgets.QWidget()
  scroll_widget.setLayout(action_layout)

  scroll_area = qtpy.QtWidgets.QScrollArea()
  scroll_area.setWidgetResizable(True)
  scroll_area.setWidget(scroll_widget)
  main_layout.addWidget(scroll_area)

  refresh_gui(members)
  window.setLayout(main_layout)
  window.show()
  return app.exec()

def main():
  try:
    with open('/proc/self/comm', 'r+') as f:
      f.write('perfm')
  except (FileNotFoundError, PermissionError):
    pass

  if sys.argv[1] == 'enter':
    enter()
  elif sys.argv[1] == 'exit':
    exit()
  elif sys.argv[1] == 'force_exit':
    force_exit()
  elif sys.argv[1] == 'count':
    count()
  elif sys.argv[1] == 'gui':
    sys.exit(gui())

if __name__ == '__main__':
  main()
