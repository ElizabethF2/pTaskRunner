#!/usr/bin/env python3

DEFAULT_CONFIG_PATH = '/etc/ptaskrunner.toml'
WAIT_POLLING_DELAY = 3

default_config = {
  'snooze_timeout': '1 hour',
  'logout_users': False,
  'delay_before_logout': 1,
  'delay_after_logout': 1,
  'reexec_init': True,
  'udev_reload': True,
  'runtime_roots': ('/run/user',),
  'pre_stop_cmds': (),
  'post_restore_cmds': (),
}

li = type('LazyImporter', (), {'__getattr__': lambda _, i: __import__(i)})()

def _parse_cmd(cmd):
  return tuple(li.shlex.split(cmd) if type(cmd) is str else cmd)

def run(cmd, check = True, capture_output = False):
  return li.subprocess.run(_parse_cmd(cmd),
                           capture_output = capture_output,
                           check = check)

def die(rc = 1, msg = None):
  if msg:
    li.sys.stderr.write(msg.strip() + '\n')
    li.sys.stderr.flush()
  li.sys.exit(rc)

def make_timer_name(name):
  return f'ptaskrunner-{name}-snooze.timer'

def get_unit_status(name):
  p = run((
    'systemctl', 'status', '--output=json', name
  ), capture_output = True, check = False)
  try:
    return li.json.loads(p.stdout.splitlines()[-1]), p.returncode
  except IndexError:
    return None, p.returncode

def get_remaining_time(ctx, name):
  status, rc = get_unit_status(make_timer_name(name))
  if not status or rc != 0:
    return None
  start_time = status['_SOURCE_REALTIME_TIMESTAMP']
  end_time = start_time + get_snooze_duration(ctx, name)
  remaining = end_time - li.time.time()

def is_unit_running(name):
  status, rc = get_unit_status(name)
  return status is not None and rc in (0, 3)

def is_profile_running(name):
  return  is_unit_running(make_timer_name(name))

def get_config(ctx, key, default = None):
  if (cfg := getattr(ctx, 'config', None)) is None:
    config_path = getattr(ctx, 'config_path', DEFAULT_CONFIG_PATH)
    try:
      with open(config_path, 'rb') as f:
        cfg = li.tomllib.load(f)
    except FileNotFoundError:
      cfg = {}
    ctx.config = cfg
  return cfg.get(key, default)

def get_profile_setting(ctx, profile_name, setting_name):
  return get_config(ctx, 'profiles', {})[profile_name].get(
    setting_name,
    default_config.get(setting_name),
  )

def get_snooze_duration(ctx, name):
  t = get_profile_setting(ctx, name, 'snooze_timeout')
  if type(t) is int:
    return t
  value, units = t.split()
  value = float(value)
  units = units.lower()
  if units in ('day', 'days', 'd', 'day(s)'):
    return (24*60*60) * value
  elif units in ('hour', 'hours', 'h', 'hr', 'hrs', 'hour(s)'):
    return (60*60) * value
  elif units in ('minute', 'minutes', 'm', 'minute(s)',
                 'min', 'mins', 'min(s)'):
    return 60 * value
  elif units in ('second', 'seconds', 's', 'second(s)',
                 'sec', 'secs', 'sec(s)'):
    return value
  raise ValueError(f'Invalid units: {repr(units)}')

def systemd_run(cmd,
                name = None,
                start_time = None,
                properties = None,
                user = None):
  if user is None:
    user_args = ()
  else:
    pw = li.pwd.getpwnam(user)
    user_args = (f'--uid={pw.pw_uid}', f'--gid={pw.pw_gid}')
  return run(
    ('systemd-run',) +
    (('--unit', name) if name else ()) +
    user_args +
    ((f'--on-calendar=@{round(start_time)}',
      f'--timer-property=AccuracySec={round(WAIT_POLLING_DELAY)}s')
      if start_time else ()) +
    tuple((f'-p{k}={v}' for k,v in (properties or {}).items())) +
    _parse_cmd(cmd)
  )

def get_commands(ctx, profile_name, kind):
  if not (cmds := get_profile_setting(ctx, profile_name, kind)):
    return ()
  if type(cmds) is not list:
    raise TypeError(
      f'Wrong type for {repr(kind)} for {repr(profile_name)}: {repr(cmds)}'
    )
  return tuple(map(_parse_cmd, cmds))

def phase1(ctx, names):
  if not names:
    raise ValueError('Must specify at least one profile name')
  seen = set()
  names = tuple((i for i in names if not (i in seen or seen.add(i))))
  snooze_duration = 0
  cmds = {}
  for name in names:
    snooze_duration = max(snooze_duration, get_snooze_duration(ctx, name))
    for k in ('task', 'user'):
      if not get_profile_setting(ctx, name, k):
        raise ValueError(f'Missing {repr(k)} for {repr(name)}')
    cmds[name] = get_commands(ctx, name, 'pre_stop_cmds')
  if not li.os.access(__file__, li.os.X_OK):
    raise Exception(f'{repr(__file__)} must be set as executable')
  for name, c in cmds.items():
    for cmd in c:
      if (proc := run(cmd, check = False)).returncode != 0:
        raise RuntimeError(f'pre_stop_cmd failed for {repr(name)}: {proc}')
  snooze_expires = li.time.time() + snooze_duration
  for name in names:
    systemd_run(
      (__file__, '--wake', name),
      name = make_timer_name(name),
      start_time = snooze_expires
    )
  for name in names:
    systemd_run(
      (__file__, '--phase2', name),
      name = f'ptaskrunner-{name}-phase2'
    )

def get_services(ctx, profile_name, kind):
  services = get_profile_setting(ctx, profile_name, kind)
  if not services:
    return ()
  if type(services) is str:
    return services.split()
  return services

def phase2(ctx, names):
  stopped_services = set()
  restarted_services = set()
  props = {}
  for name in names:
    stopped_services.update(get_services(ctx, name, 'stopped_services'))
    restarted_services.update(get_services(ctx, name, 'restarted_services'))
    props.setdefault(name, {})['ExecStopPost'] = '+' + li.shlex.join((
      'systemctl', 'start', make_timer_name(name)[:-6] + '.service'
    ))
  if any((get_profile_setting(ctx, name, 'logout_users') for name in names)):
    roots = set()
    for name in names:
      roots.update(get_profile_setting(ctx, name, 'runtime_roots'))
    users_to_logout = set()
    for root in roots:
      import os
      for uid in os.listdir(root):
        if not uid.isdigit():
          continue
        bus = os.path.join(root, uid, 'bus')
        try:
          if li.stat.S_ISSOCK(os.stat(bus).st_mode):
            users_to_logout.add((uid, bus))
        except FileNotFoundError:
          pass
    li.time.sleep(max((
      get_profile_setting(ctx, name, 'delay_before_logout') for name in names
    )))
    qdbus = li.shutil.which('qdbus') or \
            li.shutil.which('qdbus6') or \
            'qdbus5'
    for uid, bus in users_to_logout:
      run(('sudo',
           'DBUS_SESSION_BUS_ADDRESS=unix:path='+bus,
           '-u', f'#{uid}',
           qdbus,
           'org.kde.Shutdown',
           '/Shutdown',
           'org.kde.Shutdown.logout'))
    li.time.sleep(max((
      get_profile_setting(ctx, name, 'delay_after_logout') for name in names
    )))
  for service in stopped_services:
    proc = run(('systemctl', 'stop', service), check = False)
    if proc.returncode != 0 and is_unit_running(service):
      raise RuntimeError(f'Unable to stop {repr(service)}: {proc}')
  for service in (restarted_services - stopped_services):
    run(('systemctl', 'restart', service))
  for setting, cmd in (('reexec_init', ('systemctl', 'daemon-reexec')),
                       ('udev_reload', ('udevadm', 'control', '--reload'))):
    if all((get_profile_setting(ctx, name, setting) for name in names)):
      run(cmd)
  for name in names:
    systemd_run(
      get_profile_setting(ctx, name, 'task'),
      name = f'ptaskrunner-{name}-task',
      properties = props.get(name),
      user = get_profile_setting(ctx, name, 'user'),
    )

def wake(ctx, names):
  stopped_services = set()
  for name in names:
    stopped_services.update(get_services(ctx, name, 'stopped_services'))
  for service in stopped_services:
    run(('systemctl', 'start', service), check = False)
  for name in names:
    for cmd in get_commands(ctx, name, 'post_restore_cmds'):
      run(cmd)
  for name in names:
    run(('systemctl', 'stop', make_timer_name(name)))

def wait(names):
  remaining = set(names)
  while len(remaining) > 0:
    for name in names:
      if name not in remaining:
        continue
      if is_profile_running(name):
        li.time.sleep(WAIT_POLLING_DELAY)
        break
      else:
        remaining.remove(name)

def reset(ctx, names):
  for name in (names or ['*']):
    running = False if name == '*' else is_profile_running(name)
    for i in ('stop', 'reset-failed'):
      run(('systemctl', i, f'ptaskrunner-{name}-*'))
    if running:
      wake(ctx, name)

def main():
  try:
    with open('/proc/self/comm', 'r+') as f:
      f.write('ptaskrunner')
  except (FileNotFoundError, PermissionError):
    pass

  ctx = type('Context', (), {})()
  names = []
  args = list(reversed(li.sys.argv[1:]))
  while len(args) > 0:
    arg = args.pop()
    if arg in ('-c', '--config'):
      try:
        ctx.config_path = args.pop()
      except IndexError:
        die(msg = 'Must specify a path after config flag')
    elif arg[:2] == '--' and arg[2:] in ('wake', 'phase2',
                                         'is-running', 'all-running',
                                         'wait', 'run-wait',
                                         'remaining', 'reset'):
      setattr(ctx, arg[2:], True)
    else:
      names.append(arg)
  if hasattr(ctx, 'all-running'):
    die(0 if all((is_profile_running(i) for i in (names or ['*']))) else 1)
  elif hasattr(ctx, 'is-running'):
    die(0 if any((is_profile_running(i) for i in (names or ['*']))) else 1)
  elif hasattr(ctx, 'remaining'):
    for name in names:
      print(li.json.dumps(get_remaining_time(ctx, name)))
  elif hasattr(ctx, 'wait'):
    wait(names)
  elif hasattr(ctx, 'wake'):
    wake(ctx, names)
  elif hasattr(ctx, 'phase2'):
    phase2(ctx, names)
  elif hasattr(ctx, 'run-wait'):
    to_run = tuple(filter(lambda name: not is_profile_running(name), names))
    phase1(ctx, to_run)
    wait(names)
  elif hasattr(ctx, 'reset'):
    reset(ctx, names)
  else:
    phase1(ctx, names)

if __name__ == '__main__':
  main()
