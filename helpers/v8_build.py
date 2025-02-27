# -*- coding: utf-8 -*-"
import argparse
import errno
import glob
import json
import logging
import os
import os.path
import subprocess
import sys
from contextlib import contextmanager

logging.basicConfig()
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
V8_VERSION = "branch-heads/9.1"


def local_path(path="."):
    """ Return path relative to this file
    """
    return os.path.abspath(os.path.join(ROOT_DIR, path))


PATCHES_PATH = local_path('../patches')


def call(cmd):
    LOGGER.debug("Calling: '%s' from working directory %s", cmd, os.getcwd())
    current_env = os.environ
    depot_tools_env = os.pathsep.join([local_path("../py_mini_racer/extension/depot_tools"), os.environ['PATH']])
    current_env['PATH'] = depot_tools_env
    current_env['DEPOT_TOOLS_WIN_TOOLCHAIN'] = '0'
    return subprocess.check_call(cmd, shell=True, env=current_env)


@contextmanager
def chdir(new_path, make=False):
    old_path = os.getcwd()

    if make is True:
        try:
            os.mkdir(new_path)
        except OSError:
            pass

    try:
        yield os.chdir(new_path)
    finally:
        os.chdir(old_path)


def install_depot_tools():
    if not os.path.isdir(local_path("../py_mini_racer/extension/depot_tools")):
        LOGGER.debug("Cloning depot tools")
        call("git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git {}".format(local_path("../py_mini_racer/extension/depot_tools")))
    else:
        LOGGER.debug("Using already cloned depot tools")


def prepare_workdir():
    directories = ["build", "build_overrides", "buildtools", "testing",
                   "third_party", "tools", "gni"]
    with chdir(local_path("../py_mini_racer/extension")):
        for item in directories:
            if not os.path.exists(item):
                symlink_force(os.path.join("v8", item), item)


def ensure_v8_src(revision):
    """ Ensure that v8 src are presents and up-to-date
    """
    path = local_path("../py_mini_racer/extension")

    if not os.path.isfile(local_path("../py_mini_racer/extension/.gclient")):
        fetch_v8(path)
    else:
        update_v8(path)

    checkout_v8_version(local_path("../py_mini_racer/extension/v8"), revision)
    dependencies_sync(path)


def fetch_v8(path):
    """ Fetch v8
    """
    with chdir(os.path.abspath(path), make=True):
        call("fetch --nohooks v8")


def update_v8(path):
    """ Update v8 repository
    """
    with chdir(path):
        call("gclient fetch")


def checkout_v8_version(path, revision):
    """ Ensure that we have the right version
    """
    with chdir(path):
        call("git checkout {} -- .".format(revision))


def dependencies_sync(path):
    """ Sync v8 build dependencies
    """
    with chdir(path):
        call("gclient sync")


def run_hooks(path):
    """ Run v8 build hooks
    """
    with chdir(path):
        call("gclient runhooks")


def gen_makefiles(build_path, no_sysroot=False):
    with chdir(local_path("../py_mini_racer/extension")):
        build_path = local_path(build_path)
        if not os.path.exists(build_path):
            os.makedirs(build_path)
        LOGGER.debug("Writing args.gn in %s", build_path)
        with open(os.path.join(build_path, "args.gn"), "w") as f:
            opts = {
                "proprietary_codecs": "false",
                "toolkit_views": "false",
                "use_aura": "false",
                "use_dbus": "false",
                "use_gio": "false",
                "use_glib": "false",
                "use_ozone": "false",
                "use_udev": "false",
                "is_desktop_linux": "false",
                
                 "target_cpu": "\"arm64\"",
                "v8_target_cpu": "\"arm64\"",
                "cc_wrapper": "\"ccache\"",

                "is_cfi": "false",
                "is_debug": "false",
                "is_component_build": "false",

                "symbol_level": "0",
                "strip_debug_info": "true",
                "treat_warnings_as_errors": "true",

                "v8_monolithic": "false",
                "v8_use_external_startup_data": "false",
                "v8_enable_i18n_support": "false",

                "v8_untrusted_code_mitigations": "false",
                # See https://v8.dev/docs/untrusted-code-mitigations

                # See cc_wrapper
                "clang_use_chrome_plugins": "false",
            }
            if no_sysroot:
                opts.update({
                    "treat_warnings_as_errors": "false",
                    "use_sysroot": "false",
                    "clang_use_chrome_plugins": "false",
                    "clang_base_path": "\"/usr\"",
                    "use_custom_libcxx": "false",
                    "use_gold": "true",
                    "use_lld": "false",
                })
            sccache = os.environ.get('SCCACHE')
            if sccache is not None:
                opts["cc_wrapper"] = json.dumps(sccache)
            f.write("# This file is auto generated by v8_build.py")
            f.write("\n".join("{}={}".format(a, b) for (a, b) in opts.items()))
            f.write("\n")
            extra_args = os.getenv("GN_ARGS")
            if extra_args:
                f.write("\n".join(extra_args.split()))
                f.write("\n")
        call("gn gen {}".format(local_path(build_path)))


def make(build_path, target, cmd_prefix=""):
    """ Create a release of v8
    """
    with chdir(local_path("../py_mini_racer/extension")):
        call("{} ninja -vv -C {} {}".format(cmd_prefix, local_path(build_path), target))


def patch_v8():
    """ Apply patch on v8
    """
    path = local_path("../py_mini_racer/extension/v8")
    patches_paths = PATCHES_PATH
    apply_patches(path, patches_paths)


def symlink_force(target, link_name):
    LOGGER.debug("Creating symlink to %s on %s", target, link_name)
    if sys.platform == "win32":
        call(["mklink", "/d", os.path.abspath(link_name), os.path.abspath(target)])
    else:
        try:
            os.symlink(target, link_name)
        except OSError as e:
            if e.errno == errno.EEXIST:
                os.remove(link_name)
                os.symlink(target, link_name)
            else:
                raise e


def fixup_libtinfo(dir):
    dirs = ['/lib64', '/usr/lib64', '/lib', '/usr/lib']

    v5_locs = ["{}/libtinfo.so.5".format(d) for d in dirs]
    found_v5 = next((f for f in v5_locs if os.path.isfile(f)), None)
    if found_v5 and os.stat(found_v5).st_size > 100:
        return ''

    v6_locs = ["{}/libtinfo.so.6".format(d) for d in dirs]
    found_v6 = next((f for f in v6_locs if os.path.isfile(f)), None)
    if not found_v6:
        return ''

    symlink_force(found_v6, os.path.join(dir, 'libtinfo.so.5'))
    return "LD_LIBRARY_PATH='{}:{}'"\
        .format(dir, os.getenv('LD_LIBRARY_PATH', ''))


def apply_patches(path, patches_path):
    with chdir(path):

        if not os.path.isfile('.applied_patches'):
            open('.applied_patches', 'w').close()

        with open('.applied_patches', 'r+') as applied_patches_file:
            applied_patches = set(applied_patches_file.read().splitlines())

            for patch in glob.glob(os.path.join(patches_path, '*.patch')):
                if patch not in applied_patches:
                    call("patch -p1 -N < {}".format(patch))
                    applied_patches_file.write(patch + "\n")


def patch_sysroot():
    with chdir(local_path("../py_mini_racer/extension/v8/build/linux/debian_sid_amd64-sysroot")):
        with open("usr/include/glob.h", "r") as f:
            header = f.read()
        s, e = header.split("sysroot-creator.sh.", 1)
        LOGGER.debug("Patching sysroot /usr/include/glob.h")
        with open("usr/include/glob.h", "w") as f:
            f.write(s)
            f.write("sysroot-creator.sh.")
            f.write("""
__asm__(".symver glob, glob@GLIBC_2.2.5");
__asm__(".symver glob64, glob64@GLIBC_2.2.5");
            """)
        LOGGER.debug("Patching sysroot /usr/include/string.h")
        with open("usr/include/string.h", "a") as f:
            f.write("""
__asm__(".symver _sys_errlist, _sys_errlist@GLIBC_2.4");
__asm__(".symver _sys_nerr, _sys_nerr@GLIBC_2.4");
__asm__(".symver fmemopen, fmemopen@GLIBC_2.2.5");
__asm__(".symver memcpy, memcpy@GLIBC_2.2.5");
__asm__(".symver posix_spawn, posix_spawn@GLIBC_2.2.5");
__asm__(".symver posix_spawnp, posix_spawnp@GLIBC_2.2.5");
__asm__(".symver sys_errlist, sys_errlist@GLIBC_2.4");
__asm__(".symver sys_nerr, sys_nerr@GLIBC_2.4");
            """)
        with open("usr/include/math.h", "r") as f:
            header = f.read()
        s, e = header.split("sysroot-creator.sh.", 1)
        LOGGER.debug("Patching sysroot /usr/include/math.h")
        with open("usr/include/math.h", "w") as f:
            f.write(s)
            f.write("sysroot-creator.sh.")
            f.write("""
__asm__(".symver exp2f, exp2f@GLIBC_2.2.5");
__asm__(".symver expf, expf@GLIBC_2.2.5");
__asm__(".symver lgamma, lgamma@GLIBC_2.2.5");
__asm__(".symver lgammaf, lgammaf@GLIBC_2.2.5");
__asm__(".symver lgammal, lgammal@GLIBC_2.2.5");
__asm__(".symver log2f, log2f@GLIBC_2.2.5");
__asm__(".symver logf, logf@GLIBC_2.2.5");
__asm__(".symver powf, powf@GLIBC_2.2.5");
            """)


def build_v8(target=None, build_path=None, revision=None, no_build=False,
             no_sysroot=False, no_update=False):
    if target is None:
        target = "v8"
    if build_path is None:
        # Must be relative to local_path()
        build_path = "../py_mini_racer/extension/out"
    if revision is None:
        revision = V8_VERSION
    install_depot_tools()
    if not no_update:
        ensure_v8_src(revision)
        patch_v8()
    if not no_sysroot and sys.platform.startswith("linux"):
        patch_sysroot()
    prepare_workdir()
    if not no_build:
        checkout_path = local_path("../py_mini_racer/extension/v8")
        cmd_prefix = fixup_libtinfo(checkout_path)
        gen_makefiles(build_path, no_sysroot=no_sysroot)
        make(build_path, target, cmd_prefix)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="v8", help="Ninja target")
    parser.add_argument("--build-path", default="../py_mini_racer/extension/out", help="Build destination directory (relative to the current path)")
    parser.add_argument("--v8-revision", default=V8_VERSION)
    parser.add_argument("--no-build", action="store_true", help="Only prepare workdir")
    parser.add_argument("--no-update", action="store_true", help="Do not update the workdir")
    parser.add_argument("--no-sysroot", action="store_true", help="Do not use the V8 build sysroot")
    args = parser.parse_args()
    build_v8(target=args.target, build_path=args.build_path, revision=args.v8_revision,
             no_build=args.no_build, no_update=args.no_update, no_sysroot=args.no_sysroot)
