#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# oms-fprint-manager — GUI manager for the OMS match-on-chip fingerprint
# reader (USB 33a7:2388).
#
# Features (增 / 删 / 改):
#   * list enrolled fingerprints for the current user (fprintd over D-Bus)
#   * add: enroll a new finger with live stage feedback
#   * delete: remove one enrolled finger (deletes the on-chip template)
#   * modify: rename which finger a template is associated with, and re-map
#     chip slots that carry templates but no fprintd association
#     (via the privileged /usr/sbin/oms-import helper)
#
# License: LGPL-2.1-or-later
#
import json
import os
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

APP_ID = "io.github.omsmoc.FprintManager"
FPRINT_NAME = "net.reactivated.Fprint"
MANAGER_PATH = "/net/reactivated/Fprint/Manager"

# (finger code, fprintd/dbus name, Chinese label)
FINGERS = [
    (1, "left-thumb", "左手拇指"),
    (2, "left-index-finger", "左手食指"),
    (3, "left-middle-finger", "左手中指"),
    (4, "left-ring-finger", "左手无名指"),
    (5, "left-little-finger", "左手小指"),
    (6, "right-thumb", "右手拇指"),
    (7, "right-index-finger", "右手食指"),
    (8, "right-middle-finger", "右手中指"),
    (9, "right-ring-finger", "右手无名指"),
    (10, "right-little-finger", "右手小指"),
]
BY_CODE = {c: (d, z) for c, d, z in FINGERS}
BY_DBUS = {d: (c, z) for c, d, z in FINGERS}

ENROLL_RETRY = {
    "enroll-swipe-too-short",
    "enroll-finger-not-centered",
    "enroll-remove-and-retry",
    "enroll-too-fast",
    "enroll-retry-scan",
}
ENROLL_MESSAGES = {
    "enroll-swipe-too-short": "按压时间太短，请重新按压",
    "enroll-finger-not-centered": "手指没有放正，请重新按压",
    "enroll-remove-and-retry": "请抬起手指后重新按压",
    "enroll-too-fast": "操作太快，请放慢一点",
    "enroll-retry-scan": "未识别，请重新按压",
    "enroll-duplicate": "这枚指纹已经录入过了（芯片查重）",
    "enroll-data-full": "芯片存储已满（最多 16 枚）",
    "enroll-disconnected": "设备连接中断",
    "enroll-unknown-error": "录入失败（未知错误）",
    "enroll-failed": "录入失败",
}

MANAGER_XML = """
<node>
  <interface name="net.reactivated.Fprint.Manager">
    <method name="GetDefaultDevice">
      <arg type="o" name="device" direction="out"/>
    </method>
  </interface>
</node>
"""

DEVICE_XML = """
<node>
  <interface name="net.reactivated.Fprint.Device">
    <method name="ListEnrolledFingers">
      <arg type="s" name="username" direction="in"/>
      <arg type="as" name="enrolled_fingers" direction="out"/>
    </method>
    <method name="DeleteEnrolledFinger">
      <arg type="s" name="finger_name" direction="in"/>
    </method>
    <method name="Claim">
      <arg type="s" name="username" direction="in"/>
    </method>
    <method name="Release"/>
    <method name="EnrollStart">
      <arg type="s" name="finger_name" direction="in"/>
    </method>
    <method name="EnrollStop"/>
    <signal name="EnrollStatus">
      <arg type="s" name="result"/>
      <arg type="b" name="done"/>
    </signal>
    <property name="name" type="s" access="read"/>
    <property name="num-enroll-stages" type="i" access="read"/>
    <property name="scan-type" type="s" access="read"/>
  </interface>
</node>
"""


def find_helper():
    env = os.environ.get("OMS_IMPORT_HELPER")
    if env and os.path.exists(env):
        return env
    if os.path.exists("/usr/sbin/oms-import"):
        return "/usr/sbin/oms-import"
    here = os.path.dirname(os.path.realpath(__file__))
    dev = os.path.join(os.path.dirname(here), "helper", "oms-import")
    if os.path.exists(dev):
        return dev
    return None


def run_helper(args, root=True, timeout=180):
    """Run the oms-import helper (via pkexec when root=True).

    Returns (returncode, stdout, stderr).
    """
    helper = find_helper()
    if helper is None:
        return 127, "", "找不到 oms-import 工具"
    cmd = (["pkexec", helper] if root else [helper]) + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "操作超时"
    except OSError as e:
        return 126, "", str(e)


def run_helper_async(args, callback, root=True):
    def worker():
        try:
            rc, out, err = run_helper(args, root=root)
        except Exception:
            import traceback
            traceback.print_exc()
            rc, out, err = 125, "", "内部错误"
        GLib.idle_add(callback, rc, out, err)

    threading.Thread(target=worker, daemon=True).start()


def helper_error_text(code, stderr):
    if code in (126, 127):
        return "授权被取消或失败（需要管理员权限）。"
    if code == 124:
        return "操作超时。"
    msg = (stderr or "").strip()
    if "Request dismissed" in msg:
        return "授权被取消。"
    return msg if msg else f"命令失败（退出码 {code}）。"


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="OMS 指纹管理器",
                         default_width=560, default_height=640)
        self.username = GLib.get_user_name()
        self.device_path = None
        self.device_proxy = None
        self.enrolled = []

        self.toolbar = Adw.ToolbarView()
        self.set_content(self.toolbar)

        header = Adw.HeaderBar()
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                      tooltip_text="刷新")
        self.refresh_btn.connect("clicked", lambda *_: self.refresh())
        header.pack_end(self.refresh_btn)
        self.toolbar.add_top_bar(header)

        self.overlay = Adw.ToastOverlay()
        self.toolbar.set_content(self.overlay)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(18)
        box.set_margin_start(12)
        box.set_margin_end(12)
        clamp = Adw.Clamp(maximum_size=560, child=box)
        self.overlay.set_child(clamp)

        self.banner = Adw.Banner(revealed=False)
        box.append(self.banner)

        self.status_group = Adw.PreferencesGroup(title="设备")
        self.device_row = Adw.ActionRow(title="正在检测设备…", subtitle="")
        self.device_row.add_prefix(Gtk.Image.new_from_icon_name("auth-fingerprint-symbolic"))
        self.status_group.add(self.device_row)
        box.append(self.status_group)

        self.list_group = Adw.PreferencesGroup(title="已录入指纹")
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.list_group.add(self.listbox)
        box.append(self.list_group)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                          halign=Gtk.Align.CENTER, homogeneous=True)
        self.add_btn = Gtk.Button(label="新增指纹")
        self.add_btn.add_css_class("suggested-action")
        self.add_btn.connect("clicked", self.on_add)
        actions.append(self.add_btn)

        self.chip_btn = Gtk.Button(label="芯片模板…")
        self.chip_btn.connect("clicked", self.on_chip_templates)
        actions.append(self.chip_btn)
        box.append(actions)

        tip = Gtk.Label(xalign=0, wrap=True)
        tip.set_text("提示：「删除」会同时删除芯片内的模板（不可恢复）。\n"
                     "「芯片模板…」用于查看/关联芯片里已有的模板（例如 Windows 时期录入的指纹），"
                     "不重新录入、只改关联名称/归属。")
        tip.add_css_class("dim-label")
        box.append(tip)

        self.refresh()

    # ------------------------------------------------------------- dbus
    @staticmethod
    def _iface_info(xml):
        return Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]

    def get_device_proxy(self):
        if self.device_proxy is not None:
            return self.device_proxy

        manager_info = self._iface_info(MANAGER_XML)
        manager = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, manager_info,
            FPRINT_NAME, MANAGER_PATH, "net.reactivated.Fprint.Manager", None)
        res = manager.call_sync("GetDefaultDevice", None,
                                Gio.DBusCallFlags.NONE, 5000, None)
        self.device_path = res.unpack()[0]

        device_info = self._iface_info(DEVICE_XML)
        self.device_proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, device_info,
            FPRINT_NAME, self.device_path, "net.reactivated.Fprint.Device", None)
        return self.device_proxy

    def refresh(self):
        self.banner.set_revealed(False)
        try:
            proxy = self.get_device_proxy()
        except GLib.Error as e:
            self.device_row.set_title("未找到指纹设备")
            self.device_row.set_subtitle("")
            self.banner.set_title(f"无法连接 fprintd：{e.message}")
            self.banner.set_revealed(True)
            return

        name = proxy.get_cached_property("name")
        stages = proxy.get_cached_property("num-enroll-stages")
        self.device_row.set_title(name.get_string() if name else "指纹传感器")
        if stages:
            self.device_row.set_subtitle(f"按压式 ｜ {stages.get_int32()} 阶段录入")
        else:
            self.device_row.set_subtitle("")

        try:
            res = proxy.call_sync(
                "ListEnrolledFingers", GLib.Variant("(s)", (self.username,)),
                Gio.DBusCallFlags.NONE, 10000, None)
            self.enrolled = list(res.unpack()[0] or [])
        except GLib.Error as e:
            self.enrolled = []
            msg = e.message or ""
            if "NoEnrolledPrints" not in msg and "Failed to discover prints" not in msg:
                self.banner.set_title(f"读取已录入指纹失败：{msg}")
                self.banner.set_revealed(True)

        while True:
            row = self.listbox.get_row_at_index(0)
            if row is None:
                break
            self.listbox.remove(row)

        if not self.enrolled:
            empty = Adw.ActionRow(title="暂无已录入指纹",
                                  subtitle="点击下方「新增指纹」录入")
            self.listbox.append(empty)
            return

        for finger in self.enrolled:
            code, zh = BY_DBUS.get(finger, (0, finger))
            row = Adw.ActionRow(title=zh, subtitle=finger)
            row.add_prefix(Gtk.Image.new_from_icon_name("auth-fingerprint-symbolic"))

            rename_btn = Gtk.Button(icon_name="document-edit-symbolic",
                                    tooltip_text="重命名/更换手指",
                                    valign=Gtk.Align.CENTER)
            rename_btn.add_css_class("flat")
            rename_btn.connect("clicked", lambda _b, f=finger: self.on_rename(f))
            row.add_suffix(rename_btn)

            del_btn = Gtk.Button(icon_name="user-trash-symbolic",
                                 tooltip_text="删除", valign=Gtk.Align.CENTER)
            del_btn.add_css_class("flat")
            del_btn.connect("clicked", lambda _b, f=finger, z=zh: self.on_delete(f, z))
            row.add_suffix(del_btn)

            self.listbox.append(row)

    def toast(self, text):
        self.overlay.add_toast(Adw.Toast(title=text))

    def free_fingers(self, exclude_dbus=None):
        used = set(self.enrolled)
        if exclude_dbus:
            used.discard(exclude_dbus)
        return [f for f in FINGERS if f[1] not in used]

    def message(self, heading, body):
        dialog = Adw.MessageDialog(transient_for=self, modal=True,
                                   heading=heading, body=body)
        dialog.add_response("ok", "确定")
        dialog.present()

    # ---------------------------------------------------------- actions
    def on_add(self, _btn):
        if not self.free_fingers():
            self.message("无法新增", "所有 10 根手指都已录入。")
            return
        EnrollDialog(self)

    def on_delete(self, finger, zh):
        dialog = Adw.MessageDialog(
            transient_for=self, modal=True,
            heading=f"删除「{zh}」？",
            body="将同时删除芯片内对应的指纹模板，删除后需要重新录入，无法恢复。")
        dialog.add_response("cancel", "取消")
        dialog.add_response("delete", "删除")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(dlg, response):
            if response != "delete":
                return
            ok, err = self.device_delete(finger)
            if ok:
                self.toast(f"已删除「{zh}」")
                self.refresh()
            else:
                self.message("删除失败", err)

        dialog.connect("response", on_response)
        dialog.present()

    def device_delete(self, finger):
        proxy = self.get_device_proxy()
        flags = Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION
        claimed = False
        try:
            proxy.call_sync("Claim", GLib.Variant("(s)", (self.username,)),
                            flags, 60000, None)
            claimed = True
            proxy.call_sync("DeleteEnrolledFinger", GLib.Variant("(s)", (finger,)),
                            flags, 30000, None)
            return True, ""
        except GLib.Error as e:
            return False, e.message or str(e)
        finally:
            if claimed:
                try:
                    proxy.call_sync("Release", None, Gio.DBusCallFlags.NONE, 5000, None)
                except GLib.Error:
                    pass

    def on_rename(self, finger):
        free = self.free_fingers(exclude_dbus=finger)
        if not free:
            self.message("无法重命名", "没有可用的目标手指。")
            return
        RenameDialog(self, finger, free)

    def on_chip_templates(self, _btn):
        ChipDialog(self)


class EnrollDialog(Adw.Window):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True,
                         title="录入新指纹", default_width=420, default_height=380)
        self.parent = parent
        self.enrolling = False
        self.stage = 0
        self.total_stages = 6
        self.signal_id = None

        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)

        group = Adw.PreferencesGroup(title="选择要录入的手指")
        self.combo = Adw.ComboRow(title="手指")
        self.model = Gtk.StringList()
        self.fingers = parent.free_fingers()
        for code, dbus, zh in self.fingers:
            self.model.append(zh)
        self.combo.set_model(self.model)
        self.combo.set_selected(0)
        group.add(self.combo)
        box.append(group)

        self.progress = Gtk.ProgressBar(show_text=False)
        self.progress.set_visible(False)
        box.append(self.progress)

        self.status = Gtk.Label(wrap=True, xalign=0)
        self.status.set_text("点击「开始录入」，然后按照提示按压、抬起手指。")
        box.append(self.status)

        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, homogeneous=True)
        self.cancel_btn = Gtk.Button(label="关闭")
        self.cancel_btn.connect("clicked", self.on_cancel)
        buttons.append(self.cancel_btn)

        self.start_btn = Gtk.Button(label="开始录入")
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.connect("clicked", self.on_start)
        buttons.append(self.start_btn)
        box.append(buttons)

        toolbar.set_content(box)
        self.connect("close-request", self._on_close)

        self.present()

    def _on_close(self, *_a):
        if self.enrolling:
            self.stop_enroll()
        return False

    def on_cancel(self, _btn):
        if self.enrolling:
            self.stop_enroll()
        self.close()

    def on_start(self, _btn):
        proxy = self.parent.get_device_proxy()
        flags = Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION
        idx = self.combo.get_selected()
        code, dbus_name, zh = self.fingers[idx]

        try:
            proxy.call_sync("Claim", GLib.Variant("(s)", (self.parent.username,)),
                            flags, 60000, None)
        except GLib.Error as e:
            self.status.set_text(f"无法占用设备：{e.message}")
            return

        stages = proxy.get_cached_property("num-enroll-stages")
        self.total_stages = stages.get_int32() if stages else 6

        self.signal_id = proxy.connect("g-signal", self.on_signal)
        try:
            proxy.call_sync("EnrollStart", GLib.Variant("(s)", (dbus_name,)),
                            flags, 30000, None)
        except GLib.Error as e:
            self._release_and_unlisten(proxy)
            self.status.set_text(f"无法开始录入：{e.message}")
            return

        self.enrolling = True
        self.stage = 0
        self.progress.set_visible(True)
        self.progress.set_fraction(0.0)
        self.combo.set_sensitive(False)
        self.start_btn.set_sensitive(False)
        self.cancel_btn.set_label("停止")
        self.status.set_text(f"请按压指纹传感器（{zh}，第 1 阶段）…")

    def on_signal(self, _proxy, _sender, signal, params):
        if signal != "EnrollStatus":
            return
        result, done = params.unpack()

        if result == "enroll-stage-passed":
            self.stage += 1
            self.progress.set_fraction(min(1.0, self.stage / max(1, self.total_stages)))
            self.status.set_text(
                f"已通过第 {self.stage}/{self.total_stages} 阶段，请再次按压并抬起手指…")
            return

        if result in ENROLL_RETRY and not done:
            self.status.set_text(ENROLL_MESSAGES.get(result, result))
            return

        self.enrolling = False
        self.start_btn.set_sensitive(True)
        self.cancel_btn.set_label("关闭")
        self.combo.set_sensitive(True)

        proxy = self.parent.get_device_proxy()
        self._release_and_unlisten(proxy)

        if result == "enroll-completed":
            self.progress.set_fraction(1.0)
            self.status.set_text("✅ 录入成功！")
            self.parent.toast("指纹录入成功")
            self.parent.refresh()
            GLib.timeout_add(700, lambda: (self.close(), False)[1])
        else:
            self.progress.set_visible(False)
            self.status.set_text("❌ " + ENROLL_MESSAGES.get(result, f"录入失败：{result}"))

    def stop_enroll(self):
        proxy = self.parent.get_device_proxy()
        try:
            proxy.call_sync("EnrollStop", None, Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            pass
        self._release_and_unlisten(proxy)
        self.enrolling = False

    def _release_and_unlisten(self, proxy):
        if self.signal_id is not None:
            try:
                proxy.disconnect(self.signal_id)
            except Exception:
                pass
            self.signal_id = None
        try:
            proxy.call_sync("Release", None, Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            pass


class RenameDialog(Adw.MessageDialog):
    def __init__(self, parent, old_dbus, free):
        old_zh = BY_DBUS.get(old_dbus, (0, old_dbus))[1]
        super().__init__(transient_for=parent, modal=True,
                         heading=f"将「{old_zh}」重命名为",
                         body="指纹模板不需要重新录入，只修改关联的名称。")
        self.parent = parent
        self.old_dbus = old_dbus
        self.free = free

        self.model = Gtk.StringList()
        for code, dbus, zh in free:
            self.model.append(zh)
        self.combo = Gtk.DropDown(model=self.model)
        self.combo.set_margin_top(12)
        self.set_extra_child(self.combo)

        self.add_response("cancel", "取消")
        self.add_response("ok", "重命名")
        self.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("ok")
        self.set_close_response("cancel")
        self.connect("response", self.on_response)
        self.present()

    def on_response(self, _dlg, response):
        if response != "ok":
            return
        idx = self.combo.get_selected()
        old_code = BY_DBUS[self.old_dbus][0]
        new_code = self.free[idx][0]

        def done(rc, stdout, stderr):
            if rc == 0:
                self.parent.toast("重命名成功")
                self.parent.refresh()
            else:
                self.parent.message("重命名失败", helper_error_text(rc, stderr))
            return False

        run_helper_async([self.parent.username, "--rename",
                          f"{old_code}={new_code}"], done)


class ChipDialog(Adw.Window):
    """View chip slots and (re)assign them to fingers."""

    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True,
                         title="芯片模板", default_width=460, default_height=560)
        self.parent = parent
        self.slot_rows = {}    # slot -> {"row": ComboRow, "orig": code|None, "options": [(code,label)]}
        self.row_widgets = []  # rows we added, for removing them again

        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)
        header = Adw.HeaderBar()
        self.reload_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                     tooltip_text="重新读取")
        self.reload_btn.connect("clicked", lambda *_: self.load())
        header.pack_end(self.reload_btn)
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(18)
        box.set_margin_start(12)
        box.set_margin_end(12)

        self.info = Gtk.Label(wrap=True, xalign=0)
        self.info.add_css_class("dim-label")
        box.append(self.info)

        self.group = Adw.PreferencesGroup(title="芯片内模板（槽位）")
        box.append(self.group)

        self.status = Gtk.Label(wrap=True, xalign=0, visible=False)
        box.append(self.status)

        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, homogeneous=True)
        close_btn = Gtk.Button(label="关闭")
        close_btn.connect("clicked", lambda *_: self.close())
        buttons.append(close_btn)
        self.apply_btn = Gtk.Button(label="应用更改")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.set_sensitive(False)
        self.apply_btn.connect("clicked", self.on_apply)
        buttons.append(self.apply_btn)
        box.append(buttons)

        clamp = Adw.Clamp(maximum_size=460, child=box)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(clamp)
        toolbar.set_content(scroller)

        self.present()
        self.load()

    def load(self):
        for w in self.row_widgets:
            self.group.remove(w)
        self.row_widgets.clear()
        self.slot_rows.clear()
        self.apply_btn.set_sensitive(False)
        self.status.set_visible(False)
        self.info.set_text("正在通过 oms-import 读取芯片（需要授权）…")

        def done(rc, stdout, stderr):
            if rc != 0:
                self.info.set_text("读取失败：" + helper_error_text(rc, stderr))
                return False
            try:
                data = json.loads(stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                self.info.set_text("无法解析工具输出。")
                return False

            slots = [s for s in data.get("slots", []) if s.get("on_chip")]
            if not slots:
                self.info.set_text("芯片内暂无模板。先录入一枚指纹试试。")
                return False

            self.info.set_text(
                "模板存储在芯片闪存中。未关联的模板不会被系统使用，可在此关联到手指；"
                "已关联的可以更换手指（模板本身不变）。")
            for s in slots:
                self.add_slot_row(s["slot"], s.get("finger_code"))
            return False

        run_helper_async([self.parent.username, "--list"], done)

    def add_slot_row(self, slot, code):
        options = [(None, "（未关联）")] + [(c, z) for c, d, z in FINGERS]
        if code is not None:
            options = options[1:]  # mapped slots cannot be un-associated here
            options.sort(key=lambda o: (o[0] != code, o[0]))
            selected = next((i for i, (c, _z) in enumerate(options) if c == code), 0)
        else:
            selected = 0

        row = Adw.ComboRow(title=f"槽位 {slot}")
        model = Gtk.StringList()
        for c, z in options:
            model.append(z)
        row.set_model(model)
        row.set_selected(selected)
        row.connect("notify::selected", lambda *_: self.update_apply())
        self.group.add(row)
        self.row_widgets.append(row)

        self.slot_rows[slot] = {"row": row, "orig": code, "options": options}
        self.update_subtitle(slot)

    def update_subtitle(self, slot):
        info = self.slot_rows[slot]
        idx = info["row"].get_selected()
        code = info["options"][idx][0]

        if code is None:
            info["row"].set_subtitle("芯片模板，未关联任何手指")
        elif code == info["orig"]:
            info["row"].set_subtitle(f"已关联：{BY_CODE[code][1]}")
        else:
            info["row"].set_subtitle(f"将关联：{BY_CODE[code][1]}")

    def update_apply(self):
        changed = False
        seen = {}
        duplicate = None

        for slot, info in self.slot_rows.items():
            idx = info["row"].get_selected()
            code = info["options"][idx][0]
            if code is not None:
                if code in seen:
                    duplicate = (code, seen[code], slot)
                seen[code] = slot
            if code != info["orig"]:
                changed = True
            self.update_subtitle(slot)

        if duplicate:
            code, s1, s2 = duplicate
            self.status.set_text(f"⚠️ 手指「{BY_CODE[code][1]}」不能同时关联槽位 {s1} 和 {s2}。")
            self.status.set_visible(True)
            self.apply_btn.set_sensitive(False)
            return

        self.apply_btn.set_sensitive(changed)
        if changed:
            self.status.set_text("有未应用的更改。")
            self.status.set_visible(True)
        else:
            self.status.set_visible(False)

    def on_apply(self, _btn):
        args = [self.parent.username]
        for slot, info in self.slot_rows.items():
            idx = info["row"].get_selected()
            code = info["options"][idx][0]
            if code is not None and code != info["orig"]:
                args += ["--slot", f"{slot}={code}"]
        if len(args) == 1:
            return

        self.apply_btn.set_sensitive(False)
        self.status.set_text("正在应用（需要授权）…")
        self.status.set_visible(True)

        def done(rc, stdout, stderr):
            if rc == 0:
                self.parent.toast("已更新芯片模板关联")
                self.parent.refresh()
                self.close()
            else:
                self.status.set_text("应用失败：" + helper_error_text(rc, stderr))
                self.apply_btn.set_sensitive(True)
            return False

        run_helper_async(args, done)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.win = None

    def do_activate(self):
        if self.win is None:
            self.win = MainWindow(self)
        self.win.present()

        quit_after = os.environ.get("OMS_GUI_QUIT_AFTER")
        if quit_after:
            GLib.timeout_add_seconds(int(quit_after), lambda: (self.quit(), False)[1])


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
