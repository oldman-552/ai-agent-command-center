#!/usr/bin/env bash
# Build the services the test-suite needs on a machine that has NO Docker, NO package manager access and only a C
# compiler: CPython 3.12, Redis 7, PostgreSQL 16, Erlang/OTP 26 + RabbitMQ 3.12 and a headless Chromium.
# Everything goes under $ACC_TOOLCHAIN (default ~/.local/acc-toolchain); nothing is installed system-wide.
#
#   toolchain.sh python      # sysroot headers + CPython 3.12 + uv + backend/.venv        (~10 min)
#   toolchain.sh services    # Redis + PostgreSQL                                          (~3 min)
#   toolchain.sh broker      # Erlang/OTP + RabbitMQ                                       (~15 min)
#   toolchain.sh chromium    # headless Chromium for the browser tests and Playwright E2E  (~1 min)
#   toolchain.sh all
#
# Sources come from github.com (codeload tarballs), registry.npmjs.org and pypi.org only.
# This is a developer aid for restricted environments. Normal development uses Docker (see README.md).
set -euo pipefail
TC=${ACC_TOOLCHAIN:-$HOME/.local/acc-toolchain}
SYS=$TC/sysroot
SRC=$TC/src
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
JOBS=${JOBS:-$(nproc)}
LIBDIR=/usr/lib/x86_64-linux-gnu
mkdir -p "$TC" "$SRC" "$SYS/include" "$SYS/lib" "$TC/log"

fetch() {  # fetch <owner/repo> <ref> <dir>   (ref: a tag name)
  local dest=$SRC/$3
  [ -d "$dest" ] && return 0
  mkdir -p "$dest"
  curl -fsSL --retry 3 "https://codeload.github.com/$1/tar.gz/refs/tags/$2" | tar -xz -C "$dest" --strip-components=1
}

sysroot() {
  [ -f "$SYS/.done" ] && return 0
  # Headers that match the shared libraries already on the system (Debian 12): zlib 1.2.13, libffi 3.4.4,
  # bzip2 1.0.8, xz 5.4.1, OpenSSL 3.0.x. Only headers are taken; the system .so files are linked.
  fetch madler/zlib v1.2.13 zlib;                cp "$SRC"/zlib/zlib.h "$SRC"/zlib/zconf.h "$SYS/include/"
  fetch libarchive/bzip2 bzip2-1.0.8 bzip2;      cp "$SRC"/bzip2/bzlib.h "$SYS/include/"
  fetch tukaani-project/xz v5.4.1 xz;            cp -r "$SRC"/xz/src/liblzma/api/lzma.h "$SRC"/xz/src/liblzma/api/lzma "$SYS/include/"
  fetch libffi/libffi v3.4.4 libffi
  sed -e 's/@VERSION@/3.4.4/' -e 's/@TARGET@/X86_64/' -e 's/@HAVE_LONG_DOUBLE@/1/' \
      -e 's/@HAVE_LONG_DOUBLE_VARIANT@/0/' -e 's/@FFI_EXEC_TRAMPOLINE_TABLE@/0/' \
      "$SRC"/libffi/include/ffi.h.in > "$SYS/include/ffi.h"
  cp "$SRC"/libffi/src/x86/ffitarget.h "$SYS/include/"
  local ossl; ossl=$(openssl version | awk '{print $2}')
  fetch openssl/openssl "openssl-$ossl" openssl
  (cd "$SRC/openssl" && ./Configure linux-x86_64 shared no-tests >"$TC/log/openssl-configure.log" 2>&1 \
     && make build_generated >"$TC/log/openssl-generated.log" 2>&1)
  rm -rf "$SYS/include/openssl"; cp -r "$SRC/openssl/include/openssl" "$SYS/include/openssl"
  rm -f "$SYS"/include/openssl/*.in
  ln -sf $LIBDIR/libz.so.1 "$SYS/lib/libz.so";         ln -sf $LIBDIR/libffi.so.8 "$SYS/lib/libffi.so"
  ln -sf $LIBDIR/libbz2.so.1.0 "$SYS/lib/libbz2.so";   ln -sf $LIBDIR/liblzma.so.5 "$SYS/lib/liblzma.so"
  ln -sf $LIBDIR/libssl.so.3 "$SYS/lib/libssl.so";     ln -sf $LIBDIR/libcrypto.so.3 "$SYS/lib/libcrypto.so"
  touch "$SYS/.done"
}

python() {
  sysroot
  if [ ! -x "$TC/python/bin/python3.12" ]; then
    fetch python/cpython "${CPYTHON_TAG:-v3.12.16}" cpython
    (cd "$SRC/cpython" && ./configure --prefix="$TC/python" --with-openssl="$SYS" --with-openssl-rpath=no \
        --disable-test-modules \
        CPPFLAGS="-I$SYS/include" LDFLAGS="-L$SYS/lib" \
        ZLIB_CFLAGS="-I$SYS/include" ZLIB_LIBS="-L$SYS/lib -lz" \
        LIBFFI_CFLAGS="-I$SYS/include" LIBFFI_LIBS="-L$SYS/lib -lffi" \
        BZIP2_CFLAGS="-I$SYS/include" BZIP2_LIBS="-L$SYS/lib -lbz2" \
        LIBLZMA_CFLAGS="-I$SYS/include" LIBLZMA_LIBS="-L$SYS/lib -llzma" >"$TC/log/cpython-configure.log" 2>&1 \
      && make -j"$JOBS" >"$TC/log/cpython-make.log" 2>&1 && make install >"$TC/log/cpython-install.log" 2>&1)
  fi
  "$TC/python/bin/python3.12" -c 'import ssl, zlib, ctypes, bz2, lzma; print("python ok:", ssl.OPENSSL_VERSION)'
  [ -x "$TC/python/bin/uv" ] || "$TC/python/bin/python3.12" -m pip install --quiet uv
  (cd "$REPO/backend" && UV_PYTHON="$TC/python/bin/python3.12" UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
     "$TC/python/bin/uv" sync --frozen >"$TC/log/uv-sync.log" 2>&1)
  echo "backend/.venv ready"
}

services() {
  if [ ! -x "$TC/redis/bin/redis-server" ]; then
    fetch redis/redis 7.2.17 redis
    (cd "$SRC/redis" && make -j"$JOBS" MALLOC=libc BUILD_TLS=no >"$TC/log/redis-make.log" 2>&1)
    mkdir -p "$TC/redis/bin" && cp "$SRC/redis/src/redis-server" "$SRC/redis/src/redis-cli" "$TC/redis/bin/"
  fi
  if [ ! -x "$TC/pg/node_modules/@embedded-postgres/linux-x64/native/bin/postgres" ]; then
    mkdir -p "$TC/pg" && (cd "$TC/pg" && npm install --silent --no-audit --no-fund \
        @embedded-postgres/linux-x64@16.15.0-beta.19 >"$TC/log/pg-npm.log" 2>&1)
  fi
  "$TC/redis/bin/redis-server" --version
  "$TC/pg/node_modules/@embedded-postgres/linux-x64/native/bin/postgres" --version
}

broker() {
  sysroot
  if [ ! -x "$TC/erlang/bin/erl" ]; then
    fetch erlang/otp OTP-26.2.5.21 otp
    local without=""
    for a in wx odbc jinterface megaco diameter snmp ssh ftp tftp observer debugger dialyzer et reltool common_test \
             edoc erl_docgen; do without="$without --without-$a"; done
    # shellcheck disable=SC2086
    (cd "$SRC/otp" && ./configure --prefix="$TC/erlang" --without-termcap --with-ssl="$SYS" $without \
        >"$TC/log/otp-configure.log" 2>&1 && make -j"$JOBS" >"$TC/log/otp-make.log" 2>&1 \
      && make install >"$TC/log/otp-install.log" 2>&1)
  fi
  "$TC/erlang/bin/erl" -noshell -eval 'io:format("erlang ok: ~s crypto ~p~n", [erlang:system_info(otp_release), crypto:info_lib()]), halt().'
  if [ ! -d "$TC/rabbitmq/plugins" ]; then
    # RabbitMQ's release tarball is a GitHub release asset (not reachable here). The launcher scripts come from the
    # server repository at the same tag; the compiled 3.12.14 plugins tree from a public mirror of that release.
    fetch rabbitmq/rabbitmq-server v3.12.14 rabbitmq-server
    mkdir -p "$TC/rabbitmq/sbin"
    cp "$SRC"/rabbitmq-server/deps/rabbit/scripts/rabbitmq-* "$TC/rabbitmq/sbin/" && chmod +x "$TC"/rabbitmq/sbin/*
    rm -rf "$SRC/rmq-plugins" && git clone --quiet --depth 1 --filter=blob:none --sparse \
        https://github.com/xibeichina/dockerfiles.git "$SRC/rmq-plugins"
    (cd "$SRC/rmq-plugins" && git sparse-checkout set rabbitmq-3.12-management-plugins/plugins)
    cp -r "$SRC/rmq-plugins/rabbitmq-3.12-management-plugins/plugins" "$TC/rabbitmq/plugins"
  fi
  ls "$TC/rabbitmq/plugins" | grep -c . | xargs echo "rabbitmq plugins:"
}

chromium() {
  if [ ! -x "$TC/chromium/bin/chromium" ]; then
    mkdir -p "$TC/chromium/pkg" "$TC/chromium/bin"
    (cd "$TC/chromium/pkg" && npm install --silent --no-audit --no-fund @sparticuz/chromium@153.0.0 \
        >"$TC/log/chromium-npm.log" 2>&1)
    node - "$TC/chromium/pkg/node_modules/@sparticuz/chromium/bin" "$TC/chromium/bin" <<'JS'
const fs = require("fs"), path = require("path"), zlib = require("zlib"), { execFileSync } = require("child_process");
const [src, out] = process.argv.slice(2);
for (const f of fs.readdirSync(src)) {
  if (!f.endsWith(".br")) continue;
  const data = zlib.brotliDecompressSync(fs.readFileSync(path.join(src, f)));
  const name = f.slice(0, -3);
  if (name.endsWith(".tar")) {
    const tar = path.join(out, name);
    fs.writeFileSync(tar, data);
    execFileSync("tar", ["-xf", tar, "-C", out]);
    fs.unlinkSync(tar);
  } else {
    fs.writeFileSync(path.join(out, name), data, { mode: 0o755 });
  }
}
JS
  fi
  # The package's own fonts.conf points at AWS Lambda paths (/var/task, /opt). Without a font Chromium lays text
  # out with zero height and element.innerText is empty — so write a config for the fonts that were just unpacked.
  mkdir -p "$TC/chromium/bin/x-fonts" "$TC/chromium/fontcache"
  cat > "$TC/chromium/bin/x-fonts/fonts.conf" <<CONF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>$TC/chromium/bin/fonts</dir>
  <cachedir>$TC/chromium/fontcache</cachedir>
  <config></config>
</fontconfig>
CONF
  cat > "$TC/chromium/chrome" <<WRAP
#!/usr/bin/env bash
# Headless Chromium for tests. --no-sandbox: this build has no usable namespace sandbox in the container.
D=$TC/chromium/bin
export LD_LIBRARY_PATH="\$D:\$D/lib:\$D/al2023/lib:\$D/swiftshader\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export FONTCONFIG_PATH="\$D/x-fonts"
exec "\$D/chromium" --no-sandbox --disable-gpu --disable-dev-shm-usage "\$@"
WRAP
  chmod +x "$TC/chromium/chrome"
  ls "$TC/chromium/bin" | head -20
}

case "${1:-}" in
  sysroot|python|services|broker|chromium) "$1" ;;
  all) python; services; broker; chromium ;;
  *) sed -n '2,14p' "$0"; exit 2 ;;
esac
