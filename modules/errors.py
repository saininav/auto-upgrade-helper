#!/usr/bin/env python
# vim: set ts=4 sw=4 et:
#
# Copyright (c) 2013 - 2014 Intel Corporation
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
# AUTHORS
# Laurentiu Palcu   <laurentiu.palcu@intel.com>
# Marius Avram      <marius.avram@intel.com>
#

class Error(Exception):
    def __init__(self, message=None, stdout=None, stderr=None):
        self.message = message
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self):
        return "Failed(other errors)"

class MaintainerError(Error):
    """ Class for group error that can be sent to Maintainer's """
    def __init__(self, message=None, stdout=None, stderr=None):
        super(MaintainerError, self).__init__(message, stdout, stderr)

class FetchError(Error):
    def __init__(self):
        super(FetchError, self).__init__("do_fetch failed")

    def __str__(self):
        return "Failed(do_fetch)"

class PatchError(MaintainerError):
    def __init__(self):
        super(PatchError, self).__init__("do_patch failed")

    def __str__(self):
        return "Failed(do_patch)"

class ConfigureError(MaintainerError):
    def __init__(self):
        super(ConfigureError, self).__init__("do_configure failed")

    def __str__(self):
        return "Failed(do_configure)"

class CompilationError(MaintainerError):
    def __init__(self):
        super(CompilationError, self).__init__("do_compile failed")

    def __str__(self):
        return "Failed(do_compile)"

class PackageError(MaintainerError):
    def __init__(self):
        super(PackageError, self).__init__("do_package failed")

    def __str__(self):
        return "Failed(do_package)"

class LicenseError(MaintainerError):
    def __init__(self):
        super(LicenseError, self).__init__("license checksum does not match")

    def __str__(self):
        return "Failed(license issue)"

class UnsupportedProtocolError(Error):
    def __init__(self):
        super(UnsupportedProtocolError, self).__init__("SRC_URI protocol not supported")

    def __str__(self):
        return "Failed(Unsupported protocol)"

class UpgradeNotNeededError(Error):
    def __init__(self):
        super(UpgradeNotNeededError, self).__init__("Recipe already up to date")

    def __str__(self):
        return "Failed(up to date)"

class EmptyEnvError(Error):
    def __init__(self, stdout):
        super(EmptyEnvError, self).__init__("Empty environment returned", stdout)

    def __str__(self):
        return "Failed(get_env)"

class IntegrationError(Error):
    def __init__(self, stdout, pkg_ctx):
        super(IntegrationError, self).__init__("Failed to build %s in testimage branch"
                % pkg_ctx['PN'], stdout)
        self.pkg_ctx = pkg_ctx

        def __str__(self):
            return "Failed(integrate)"
