# -*- coding: utf-8 -*-
"""冷抢红包 — 极简版（带毫秒时间戳）

  收到红包 → Fill → Service_StartReceive → Launch_receive(1581)
           → 拿 timing → Service_StartOpen → Launch_open(1685)

  只 hook 两个点：RECV / ParseExtra
  Launch 必须 setTimeout 延后（不能在 hook 栈上调）

  python python/tools/frida_cold_hongbao_auto.py          # 真抢
  python python/tools/frida_cold_hongbao_auto.py --dry    # 只看日志不发包
"""
from __future__ import annotations
import argparse, sys, time
from datetime import datetime
import frida
try:
    import psutil
except ImportError:
    psutil = None

JS = r"""
'use strict';
const DRY = %DRY%;
const B = {
  recv: 0x166A3C0, fill: 0x9D8F80,
  svcRecv: 0x2974A70, svcOpen: 0x2974A80,
  launchRecv: 0x4A64BB0, launchOpen: 0x4A6C0A0,
  parseExtra: 0x5B9C2A0,
  getLocator: 0x01F460, resolveType: 0x1A7A30,
  typeKey: 0x813AD78, svcVt: 0x84E2988,
};

let base, F = {}, svc = null;
const pins = {}, claimed = {}, pending = {}, opened = {};
/* sendId → Date.now() at claim */
const t0 = {};
const tLaunch = {};

function now() { return Date.now(); }
function L(t, s, sid) {
  const ts = now();
  let extra = '';
  if (sid && t0[sid]) {
    extra = ' +' + (ts - t0[sid]) + 'ms';
    if (tLaunch[sid] && (t === 'res' || t === 'timing' || t === '1685' || t === 'open-build')) {
      extra += ' launch+' + (ts - tLaunch[sid]) + 'ms';
    }
  }
  send({ tag: t, text: s + extra, ts: ts, sid: sid || '' });
}
function pin(p, id) { if (p) (pins[id] = pins[id] || []).push(p); return p; }
function z(n, id) {
  const m = Memory.alloc(n);
  m.writeByteArray(new Uint8Array(n));
  return pin(m, id);
}
function heap(p) {
  try { return p && !p.isNull() && p.compare(ptr(0x10000)) > 0; } catch (e) { return false; }
}

// MSVC std::string
function rstr(a) {
  try {
    const n = a.add(16).readU64().toNumber();
    if (n <= 0 || n > 0x10000) return '';
    const cap = a.add(24).readU64();
    const d = cap.compare(15) > 0 ? a.readPointer() : a;
    return d.readUtf8String(n) || '';
  } catch (e) { return ''; }
}
function wstr(s, id) {
  const n = s.length, h = Memory.alloc(0x20);
  h.writeByteArray(new Uint8Array(0x20));
  if (n <= 15) {
    for (let i = 0; i < n; i++) h.add(i).writeU8(s.charCodeAt(i));
    h.add(16).writeU64(n); h.add(24).writeU64(15);
  } else {
    const d = Memory.alloc(n + 1);
    for (let i = 0; i < n; i++) d.add(i).writeU8(s.charCodeAt(i));
    d.add(n).writeU8(0);
    pin(d, id); h.writePointer(d);
    h.add(16).writeU64(n); h.add(24).writeU64(n);
  }
  return pin(h, id);
}
function patchStr(a, s, id) {
  const n = s.length;
  if (n <= 15) {
    for (let i = 0; i < n; i++) a.add(i).writeU8(s.charCodeAt(i));
    a.add(n).writeU8(0);
    a.add(16).writeU64(n); a.add(24).writeU64(15);
  } else {
    const d = Memory.alloc(n + 1);
    for (let i = 0; i < n; i++) d.add(i).writeU8(s.charCodeAt(i));
    d.add(n).writeU8(0); pin(d, id);
    a.writePointer(d); a.add(16).writeU64(n); a.add(24).writeU64(n);
  }
}

// 解析红包 LocalMsg → {sendId, ...}
function parseHB(msg) {
  const c = rstr(msg.add(0x160));
  if (!c || (!c.includes('wcpayinfo') && !/<type>\s*(?:<!\[CDATA\[)?2001/.test(c))) return null;
  const body = c.replace(/^wxid_\S+:\n?/, '');
  const url = (body.match(/<nativeurl>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/nativeurl>/i) || [])[1] || '';
  const q = {};
  (url.split('?')[1] || '').split('&').forEach(p => {
    const i = p.indexOf('='); if (i > 0) try { q[p.slice(0,i)] = decodeURIComponent(p.slice(i+1)); } catch(e){}
  });
  const sendId = q.sendid || q.sendId || (body.match(/<sendid>(?:<!\[CDATA\[)?([^<\]]+)/i) || [])[1];
  if (!sendId) return null;
  return { sendId, from: rstr(msg.add(0x18)), to: rstr(msg.add(0x38)) };
}

// DI 解析 HongBao service*
function getSvc() {
  if (svc) return svc;
  const loc = F.getLocator();
  if (!heap(loc)) throw new Error('locator null (未登录?)');
  const csp = Memory.alloc(0x10); csp.writeByteArray(new Uint8Array(0x10));
  new NativeFunction(loc.readPointer().add(0x60).readPointer(), 'pointer', ['pointer','pointer'])(loc, csp);
  const cont = csp.readPointer();
  const rsp = Memory.alloc(0x10); rsp.writeByteArray(new Uint8Array(0x10));
  new NativeFunction(cont.readPointer().add(0x30).readPointer(), 'pointer', ['pointer','pointer'])(cont, rsp);
  const res = rsp.readPointer();
  const tk = Memory.alloc(8); tk.writePointer(base.add(B.typeKey));
  const out = Memory.alloc(0x10); out.writeByteArray(new Uint8Array(0x10));
  F.resolve(res, out, tk);
  const s = out.readPointer();
  if (!heap(s) || !s.readPointer().equals(base.add(B.svcVt)))
    throw new Error('service resolve fail');
  svc = s;
  L('svc', s.toString());
  return svc;
}

// 阶段 A：receive
function doRecv(msg, sendId) {
  if (claimed[sendId]) return;
  claimed[sendId] = 1;
  t0[sendId] = now();
  let s;
  try { s = getSvc(); } catch (e) { L('err', e.message, sendId); return; }

  const ctx = z(0x400, sendId);
  F.fill(msg, ctx);
  if (!heap(ctx.add(0x270).readPointer())) {
    L('skip', 'app_ext null ' + sendId, sendId); return;
  }

  const out = z(0x60, sendId);
  F.svcRecv(s, out, ctx);
  const parent = out.add(0x38).readPointer();
  if (!heap(parent)) { L('err', 'parent null', sendId); return; }
  pin(parent, sendId);
  pending[sendId] = ctx;
  L('recv-ok', sendId, sendId);

  if (DRY) return;
  setTimeout(() => {
    try {
      F.launchRecv(parent, z(0x100, sendId));
      tLaunch[sendId] = now();
      L('1581', sendId, sendId);
    } catch (e) { L('err', String(e), sendId); }
  }, 0);
}

// 阶段 B：open
function doOpen(sendId, timing) {
  if (!pending[sendId] || opened[sendId]) return;
  if (!/^[0-9A-Fa-f]{32}$/.test(timing)) return;
  opened[sendId] = 1;
  const ctx = pending[sendId];
  delete pending[sendId];
  L('timing', sendId + ' ' + timing, sendId);
  if (DRY) return;

  setTimeout(() => {
    try {
      const s = getSvc();
      const out = z(0x60, sendId);
      const t = wstr(timing, sendId);
      F.svcOpen(s, out, ctx, t);
      const parent = out.add(0x38).readPointer();
      if (!heap(parent)) { L('err', 'open parent null', sendId); return; }
      pin(parent, sendId);
      if (rstr(parent.add(0x390)) !== timing) patchStr(parent.add(0x390), timing, sendId);
      L('open-build', sendId, sendId);
      setTimeout(() => {
        try {
          F.launchOpen(parent, z(0x100, sendId));
          L('1685', sendId, sendId);
        } catch (e) { L('err', String(e), sendId); }
      }, 0);
    } catch (e) { L('err', String(e), sendId); }
  }, 30);
}

function install(m) {
  base = m.base;
  const nf = (rva, ret, args) => new NativeFunction(base.add(rva), ret, args);
  F.fill     = nf(B.fill, 'pointer', ['pointer','pointer']);
  F.svcRecv  = nf(B.svcRecv, 'pointer', ['pointer','pointer','pointer']);
  F.svcOpen  = nf(B.svcOpen, 'pointer', ['pointer','pointer','pointer','pointer']);
  F.launchRecv = nf(B.launchRecv, 'pointer', ['pointer','pointer']);
  F.launchOpen = nf(B.launchOpen, 'pointer', ['pointer','pointer']);
  F.getLocator = nf(B.getLocator, 'pointer', []);
  F.resolve  = nf(B.resolveType, 'pointer', ['pointer','pointer','pointer']);

  // hook1: 收消息
  Interceptor.attach(base.add(B.recv), {
    onEnter(a) {
      try {
        const v = a[1]; if (!v || v.isNull()) return;
        const b = v.readPointer(), e = v.add(8).readPointer();
        if (!heap(b) || !heap(e) || b.compare(e) >= 0) return;
        let n = e.sub(b).toInt32() / 0x290;
        if (n < 1 || n > 64) n = 1;
        for (let i = 0; i < n; i++) {
          const msg = b.add(i * 0x290);
          const hb = parseHB(msg);
          if (hb) { L('HB', hb.sendId + ' from=' + (hb.from || ''), hb.sendId); doRecv(msg, hb.sendId); }
        }
      } catch (e) { L('err', String(e)); }
    }
  });

  // hook2: 响应 timing
  Interceptor.attach(base.add(B.parseExtra), {
    onEnter(a) { this.o = a[0]; },
    onLeave() {
      try {
        if (!this.o || this.o.isNull()) return;
        const status = rstr(this.o.add(0x10));
        const sendId = rstr(this.o.add(0x30));
        const timing = rstr(this.o.add(0x188));
        L('res', status + ' ' + sendId + ' ' + timing.slice(0, 16), sendId);
        if (status === 'ok' && pending[sendId]) doOpen(sendId, timing);
      } catch (e) { L('err', String(e)); }
    }
  });

  L('ready', DRY ? 'DRY-RUN' : 'LIVE');
  try { getSvc(); } catch (e) { L('warn', e.message + ' (收到消息时再试)'); }
}

const t = setInterval(() => {
  const m = Process.findModuleByName('Weixin.dll');
  if (m) { clearInterval(t); install(m); }
}, 200);
"""


def find_pid():
    dev = frida.get_local_device()
    cands = [p for p in dev.enumerate_processes() if p.name.lower() == "weixin.exe"]
    if not cands:
        raise SystemExit("Weixin.exe not found")
    if not psutil:
        return cands[0].pid
    return max(cands, key=lambda p: psutil.Process(p.pid).memory_info().rss).pid


def main():
    ap = argparse.ArgumentParser(description="冷抢红包 极简版（带时间戳）")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--dry", action="store_true", help="只观察，不 Launch")
    args = ap.parse_args()

    pid = args.pid or find_pid()
    print(f"[*] pid={pid}  {'DRY' if args.dry else 'LIVE'}", flush=True)
    if not args.dry:
        print("[!] 会发真实 CGI", flush=True)
    print("[*] 日志: [本地时钟] [tag] text  +claim后ms  launch+后ms", flush=True)
    print("[*] 关注: 1581 → res 之间的 launch+XXms 即 1581 网络耗时", flush=True)

    sess = frida.attach(pid)
    sc = sess.create_script(JS.replace("%DRY%", "true" if args.dry else "false"))

    def on_msg(m, _d):
        if m.get("type") == "send":
            p = m.get("payload") or {}
            wall = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            tag = p.get("tag", "?")
            text = p.get("text", "")
            print(f"[{wall}] [{tag}] {text}", flush=True)
        else:
            print("[error]", m.get("description", m), file=sys.stderr, flush=True)

    sc.on("message", on_msg)
    sc.load()
    print("[*] ready  请发一个红包到本账号/所在群；Ctrl+C 停", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] stop")
    finally:
        try:
            sess.detach()
        except Exception:
            pass


if __name__ == "__main__":
    main()
