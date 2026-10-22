"""Builds a Fen Light release from plugin.video.fenlight/:
  - packages/plugin.video.fenlight-<version>.zip, without files Kodi never uses (__pycache__, .pyc, pymysql's tests)
  - fen_light_version and packages/index.html pointing at it, which is where installed add-ons look for updates
  - only the newest KEEP_RELEASES add-on zips left in packages/, which is what the add-on's rollback offers

Usage: python release.py 1.1.31     set that version in addon.xml, then build it
       python release.py            build the version already in addon.xml
       python release.py --prune    only remove the old zips
"""
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
ADDON = 'plugin.video.fenlight'
PACKAGES = os.path.join(ROOT, 'packages')
KEEP_RELEASES = 10
SKIP_DIRS = ('__pycache__',)
SKIP_PATHS = ('resources/lib/pymysql/tests',)
SKIP_FILES = ('.pyc', '.pyo', '.DS_Store', 'Thumbs.db')
ZIP_NAME = re.compile(r'%s-([0-9.]+)\.zip' % re.escape(ADDON))


def read(path):
    with open(path, encoding='utf-8', newline='') as f:
        return f.read()


def write(path, text):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def addon_xml():
    return os.path.join(ROOT, ADDON, 'addon.xml')


def set_version(version):
    if not re.fullmatch(r'[0-9]+(\.[0-9]+)*', version):
        sys.exit('Not a version number: %s' % version)
    text, found = re.subn(r'(<addon\b[^>]*\bversion=")[^"]*(")', r'\g<1>%s\2' % version, read(addon_xml()), count=1)
    if not found:
        sys.exit('No <addon version="..."> in %s' % addon_xml())
    write(addon_xml(), text)


def current_version():
    return re.search(r'<addon\b[^>]*\bversion="([^"]+)"', read(addon_xml())).group(1)


def build_zip(version):
    source = os.path.join(ROOT, ADDON)
    def relative(path):
        return os.path.relpath(path, source).replace(os.sep, '/')
    target = os.path.join(PACKAGES, '%s-%s.zip' % (ADDON, version))
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as zf:
        for folder, dirs, files in os.walk(source):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and relative(os.path.join(folder, d)) not in SKIP_PATHS)
            zf.write(folder, ADDON if folder == source else '%s/%s' % (ADDON, relative(folder)))
            for name in sorted(files):
                if not name.endswith(SKIP_FILES):
                    zf.write(os.path.join(folder, name), '%s/%s' % (ADDON, relative(os.path.join(folder, name))))
    return target


def prune():
    """Delete all but the newest KEEP_RELEASES add-on zips; returns the deleted names"""
    zips = sorted((name for name in os.listdir(PACKAGES) if ZIP_NAME.fullmatch(name)),
                  key=lambda name: tuple(int(part) for part in ZIP_NAME.fullmatch(name).group(1).split('.')))
    old = zips[:-KEEP_RELEASES]
    for name in old:
        os.remove(os.path.join(PACKAGES, name))
    return old


def main(args):
    if args == ['--prune']:
        old = prune()
        return print('Removed %d old zips; kept the newest %d' % (len(old), KEEP_RELEASES))
    if args:
        set_version(args[0])
    version = current_version()
    target = build_zip(version)
    write(os.path.join(ROOT, 'fen_light_version'), version)
    index = os.path.join(PACKAGES, 'index.html')
    write(index, ZIP_NAME.sub('%s-%s.zip' % (ADDON, version), read(index)))
    old = prune()
    print('Built %s (%d KB)' % (os.path.relpath(target, ROOT), os.path.getsize(target) // 1024))
    print('fen_light_version and packages/index.html now point at %s; removed %d old zips' % (version, len(old)))


if __name__ == '__main__':
    main(sys.argv[1:])
