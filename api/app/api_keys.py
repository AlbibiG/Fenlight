#!/usr/bin/env python3
"""Named API keys for the Fen Light API: one per device, so one device can be locked out without re-keying the
others. They're kept in a JSON file that the API rereads whenever it changes, so changes apply at once.

On the API's machine, as root (the installer adds this as the fenlight-api-keys command):
  fenlight-api-keys                    list the keys
  fenlight-api-keys add NAME [KEY]     add a key (random unless KEY is given) and print it
  fenlight-api-keys show NAME          print one key
  fenlight-api-keys reset NAME [KEY]   give NAME a new key; the old one stops working at once
  fenlight-api-keys rename OLD NEW     rename a key
  fenlight-api-keys remove NAME        delete a key; the device using it is locked out at once
"""
import argparse
import json
import os
import secrets
import sys
import time

DEFAULT_FILE = '/etc/fenlight-api/keys.json'
NAME_LENGTH = 64


def load(path):
    """The keys, as [{'name', 'key', 'added'}, ...]; empty if the file doesn't exist yet"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)['keys']
    except FileNotFoundError:
        return []


def save(path, keys, group=None):
    """Replace the file in one step, readable only by root and the API's service group"""
    temporary = '%s.tmp' % path
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640), 'w', encoding='utf-8') as f:
        json.dump({'keys': keys}, f, indent=1)
        f.write('\n')
    os.chmod(temporary, 0o640)    # os.open's mode is reduced by the umask
    if group:
        import grp
        os.chown(temporary, 0, grp.getgrnam(group).gr_gid)
    os.replace(temporary, path)


def _find(keys, name):
    return next((entry for entry in keys if entry['name'] == name), None)


def _existing(keys, name):
    entry = _find(keys, name)
    if not entry:
        sys.exit('No key named "%s". The keys are: %s' % (name, ', '.join(k['name'] for k in keys) or 'none yet'))
    return entry


def _new_name(keys, name):
    name = name.strip()
    if not name or len(name) > NAME_LENGTH or not name.isprintable():
        sys.exit('A key name needs 1 to %d printable characters' % NAME_LENGTH)
    if _find(keys, name):
        sys.exit('There is already a key named "%s"' % name)
    return name


def _new_key(other_keys, key):
    if key is None:
        return secrets.token_urlsafe(32)
    if not key.isprintable() or any(c.isspace() for c in key):
        sys.exit('A key can only have printable characters, without spaces')
    if any(entry['key'] == key for entry in other_keys):
        sys.exit('Another device already uses that key')
    return key


def _print_keys(keys):
    if not keys:
        return print('No API keys yet. Add one for each device with: fenlight-api-keys add NAME')
    name_width = max(len(entry['name']) for entry in keys + [{'name': 'NAME'}])
    key_width = max(len(entry['key']) for entry in keys)
    print('%-*s  %-*s  %s' % (name_width, 'NAME', key_width, 'KEY', 'ADDED'))
    for entry in keys:
        print('%-*s  %-*s  %s' % (name_width, entry['name'], key_width, entry['key'], entry.get('added', '')))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='fenlight-api-keys', description='View and manage the API keys Fen Light devices '
                                     "use, one per device. Changes apply at once; the API doesn't need a restart.")
    parser.add_argument('--file', default=DEFAULT_FILE, help=argparse.SUPPRESS)
    parser.add_argument('--group', help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest='command', metavar='COMMAND')
    commands.add_parser('list', help='list the keys (the default)')
    command = commands.add_parser('add', help='add a key, random unless KEY is given, and print it')
    command.add_argument('name')
    command.add_argument('key', nargs='?')
    command = commands.add_parser('show', help='print one key')
    command.add_argument('name')
    command = commands.add_parser('reset', help='give NAME a new key, random unless KEY is given; the old one stops working')
    command.add_argument('name')
    command.add_argument('key', nargs='?')
    command = commands.add_parser('rename', help='rename a key')
    command.add_argument('old')
    command.add_argument('new')
    command = commands.add_parser('remove', help='delete a key; the device using it is locked out')
    command.add_argument('name')
    commands.add_parser('names')    # one name per line, for scripts such as the installer
    args = parser.parse_args(argv)
    try:
        keys = load(args.file)
        if args.command in (None, 'list'):
            return _print_keys(keys)
        if args.command == 'names':
            return print('\n'.join(entry['name'] for entry in keys)) if keys else None
        if args.command == 'show':
            return print(_existing(keys, args.name)['key'])
        key = None
        if args.command == 'add':
            name, key = _new_name(keys, args.name), _new_key(keys, args.key)
            keys.append({'name': name, 'key': key, 'added': time.strftime('%Y-%m-%d %H:%M')})
            message = 'Added "%s". Enter this as API Key in Fen Light on that device:' % name
        elif args.command == 'reset':
            entry = _existing(keys, args.name)
            entry['key'] = key = _new_key([k for k in keys if k is not entry], args.key)
            message = 'New key for "%s"; the old one has stopped working. Enter this as API Key in Fen Light on that device:' % entry['name']
        elif args.command == 'rename':
            entry = _existing(keys, args.old)
            entry['name'] = _new_name(keys, args.new)
            message = 'Renamed "%s" to "%s"' % (args.old, entry['name'])
        else:
            entry = _existing(keys, args.name)
            if len(keys) == 1:
                sys.exit('"%s" is the only key. Add another before removing it, or every device is locked out' % entry['name'])
            keys.remove(entry)
            message = 'Removed "%s"; the device using it is locked out' % entry['name']
        save(args.file, keys, args.group)
        # the message goes to stderr when there's a key, so `$(fenlight-api-keys add NAME)` captures just the key
        print(message, file=sys.stderr if key else sys.stdout)
        if key:
            print(key)
    except PermissionError:
        sys.exit('Permission denied: run it as root, for example with sudo')
    except (ValueError, KeyError) as exc:
        sys.exit("Can't read %s: %s" % (args.file, exc))


if __name__ == '__main__':
    main()
