// D-Bus interface exported by the ShareL extension: app.sharel.Shell on the session bus.
//
// Wayland clients can't list windows, read the pointer, place their own windows, keep them
// above, grab global keys or set the clipboard while unfocused. The shell can; this
// interface hands exactly those abilities to the ShareL app. Structured results are JSON
// strings ('s') so the Python side can evolve without signature changes.

export const BUS_NAME = 'app.sharel.Shell';
export const OBJECT_PATH = '/app/sharel/Shell';

export const INTERFACE_XML = `
<node>
  <interface name="app.sharel.Shell">
    <property name="Version" type="s" access="read"/>

    <method name="GetMonitors"><arg type="s" direction="out" name="json"/></method>
    <method name="GetWindows"><arg type="s" direction="out" name="json"/></method>
    <method name="GetActiveWindow"><arg type="s" direction="out" name="json"/></method>
    <method name="GetWindowDetails">
      <arg type="t" direction="in" name="id"/>
      <arg type="s" direction="out" name="json"/>
    </method>
    <method name="GetPointer">
      <arg type="i" direction="out" name="x"/>
      <arg type="i" direction="out" name="y"/>
      <arg type="u" direction="out" name="mods"/>
    </method>

    <method name="Activate">
      <arg type="t" direction="in" name="id"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="SetAbove">
      <arg type="t" direction="in" name="id"/>
      <arg type="b" direction="in" name="above"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="ToggleAbove">
      <arg type="t" direction="in" name="id"/>
      <arg type="b" direction="out" name="above"/>
    </method>
    <method name="MoveResize">
      <arg type="t" direction="in" name="id"/>
      <arg type="i" direction="in" name="x"/>
      <arg type="i" direction="in" name="y"/>
      <arg type="i" direction="in" name="width"/>
      <arg type="i" direction="in" name="height"/>
      <arg type="s" direction="out" name="json"/>
    </method>
    <method name="PositionWindow">
      <arg type="t" direction="in" name="id"/>
      <arg type="i" direction="in" name="x"/>
      <arg type="i" direction="in" name="y"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="FindWindows">
      <arg type="u" direction="in" name="pid"/>
      <arg type="s" direction="in" name="title"/>
      <arg type="s" direction="out" name="json"/>
    </method>
    <method name="MakeBorderless">
      <arg type="t" direction="in" name="id"/>
      <arg type="b" direction="in" name="borderless"/>
      <arg type="s" direction="out" name="json"/>
    </method>
    <method name="MakeFullscreen">
      <arg type="t" direction="in" name="id"/>
      <arg type="b" direction="in" name="fullscreen"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="SetOpacity">
      <arg type="t" direction="in" name="id"/>
      <arg type="u" direction="in" name="opacity"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="SetSticky">
      <arg type="t" direction="in" name="id"/>
      <arg type="b" direction="in" name="sticky"/>
      <arg type="b" direction="out" name="ok"/>
    </method>

    <method name="Screenshot">
      <arg type="s" direction="in" name="kind"/>
      <arg type="t" direction="in" name="target"/>
      <arg type="i" direction="in" name="x"/>
      <arg type="i" direction="in" name="y"/>
      <arg type="i" direction="in" name="width"/>
      <arg type="i" direction="in" name="height"/>
      <arg type="b" direction="in" name="cursor"/>
      <arg type="s" direction="in" name="path"/>
      <arg type="s" direction="out" name="json"/>
    </method>
    <method name="PickWindow">
      <arg type="s" direction="in" name="hint"/>
      <arg type="s" direction="out" name="json"/>
    </method>

    <method name="SetClipboard">
      <arg type="s" direction="in" name="mime"/>
      <arg type="ay" direction="in" name="data"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="GetClipboardMimetypes">
      <arg type="as" direction="out" name="mimetypes"/>
    </method>

    <method name="MouseHighlighter">
      <arg type="b" direction="in" name="on"/>
      <arg type="s" direction="in" name="style"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="ShowBorder">
      <arg type="s" direction="in" name="tag"/>
      <arg type="i" direction="in" name="x"/>
      <arg type="i" direction="in" name="y"/>
      <arg type="i" direction="in" name="width"/>
      <arg type="i" direction="in" name="height"/>
      <arg type="s" direction="in" name="style"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="HideBorder">
      <arg type="s" direction="in" name="tag"/>
      <arg type="b" direction="out" name="ok"/>
    </method>

    <method name="GrabAccelerator">
      <arg type="s" direction="in" name="accelerator"/>
      <arg type="s" direction="in" name="name"/>
      <arg type="u" direction="out" name="action"/>
    </method>
    <method name="UngrabAccelerator">
      <arg type="u" direction="in" name="action"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="UngrabAll">
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="ListAccelerators">
      <arg type="s" direction="out" name="json"/>
    </method>
    <signal name="AcceleratorActivated">
      <arg type="u" name="action"/>
      <arg type="s" name="name"/>
      <arg type="u" name="timestamp"/>
    </signal>
  </interface>
</node>`;
