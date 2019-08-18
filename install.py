# installer for tracer
# Copyright 2019 Matthew Wall
# Distributed under the terms of the GNU Public License (GPLv3)

from setup import ExtensionInstaller

def loader():
    return TracerInstaller()

class TracerInstaller(ExtensionInstaller):
    def __init__(self):
        super(TracerInstaller, self).__init__(
            version="0.1",
            name='tracer',
            description='Driver for Tracer solar charge controller',
            author="Matthew Wall",
            author_email="mwall@users.sourceforge.net",
            files=[('bin/user', ['bin/user/tracer.py'])]
            )
