import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root

  moduleName: "ibnabeeali.codex-multiplexer"
  ipcTarget: "ibnabeeali.codex-multiplexer"
  manageIpc: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color accent: Color.accent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color surface: Color.popups.background
  readonly property color cardSurface: root.alpha(foreground, 0.035)
  readonly property color track: Style.selectedFillFor(foreground, accent)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  property var payload: ({})
  readonly property var accounts: payload && Array.isArray(payload.accounts) ? payload.accounts : []
  property bool refreshing: false
  property bool refreshQueued: false
  property bool queuedTokenRefresh: false
  property string errorText: ""
  property int selectedIndex: 0
  property string detailAccountId: ""
  property double nowMs: Date.now()
  property double lastLiveRefreshMs: 0

  readonly property var detailAccount: accountById(detailAccountId)
  readonly property bool inDetail: detailAccount !== null
  readonly property bool alarming: hasAlarmingAccount()
  readonly property int refreshIntervalSec: Math.max(30, Number(setting("refreshIntervalSec", 300)))

  function clamp(value, low, high) { return Math.max(low, Math.min(high, value)) }
  function alpha(color, amount) { return Qt.rgba(color.r, color.g, color.b, amount) }

  function accountById(id) {
    for (var i = 0; i < accounts.length; i++)
      if (String(accounts[i].id || "") === String(id || "")) return accounts[i]
    return null
  }

  function hasAlarmingAccount() {
    for (var i = 0; i < accounts.length; i++) {
      var status = String(accounts[i].status || "")
      if (status === "low" || status === "exhausted" || status === "login_required"
          || status === "identity_mismatch" || status === "unavailable")
        return true
    }
    return false
  }

  function accountTone(account) {
    if (!account) return dim
    var status = String(account.status || "")
    if (status === "low" || status === "exhausted" || status === "login_required"
        || status === "identity_mismatch" || status === "unavailable")
      return urgent
    if (account.isRecommended === true) return accent
    return foreground
  }

  function limitTone(limit) {
    return limit && Number(limit.remainingPercent) <= 20 ? urgent : accent
  }

  function planLabel(account) {
    var plan = account ? String(account.plan || "") : ""
    return plan === "" ? "CODEX" : plan.toUpperCase()
  }

  function updatedLabel() {
    if (!payload || !payload.updatedAt) return refreshing ? "Refreshing…" : "Not refreshed"
    var updated = new Date(String(payload.updatedAt)).getTime()
    if (!isFinite(updated)) return "Updated"
    var elapsed = Math.max(0, nowMs - updated)
    if (elapsed < 60000) return refreshing ? "Refreshing…" : "Updated now"
    var minutes = Math.floor(elapsed / 60000)
    if (minutes < 60) return "Updated " + minutes + "m ago" + (refreshing ? " · refreshing" : "")
    var hours = Math.floor(minutes / 60)
    return "Updated " + hours + "h ago" + (refreshing ? " · refreshing" : "")
  }

  function formatDuration(ms) {
    if (!(ms > 0)) return "now"
    var minutes = Math.floor(ms / 60000)
    var hours = Math.floor(minutes / 60)
    var days = Math.floor(hours / 24)
    if (days > 0) return days + "d " + (hours % 24) + "h"
    if (hours > 0) return hours + "h " + (minutes % 60) + "m"
    return Math.max(1, minutes) + "m"
  }

  function resetLabel(limit, prefix) {
    if (!limit || limit.resetsAt === null || limit.resetsAt === undefined) return "Reset unavailable"
    var remaining = Number(limit.resetsAt) * 1000 - nowMs
    return (prefix ? "Resets in " : "reset ") + formatDuration(remaining)
  }

  function overviewMeta() {
    var count = accounts.length
    return count + " account" + (count === 1 ? "" : "s") + " · " + updatedLabel()
  }

  function applyPayload(raw, live) {
    var text = String(raw || "").trim()
    if (text === "") return false
    try {
      var parsed = JSON.parse(text)
      if (!parsed || parsed.schemaVersion !== 1 || !Array.isArray(parsed.accounts))
        throw new Error("unsupported account status record")
      if (!live && lastLiveRefreshMs > 0) return false
      payload = parsed
      errorText = ""
      if (selectedIndex >= accounts.length) selectedIndex = Math.max(0, accounts.length - 1)
      if (detailAccountId !== "" && !accountById(detailAccountId)) detailAccountId = ""
      if (live) lastLiveRefreshMs = Date.now()
      return true
    } catch (error) {
      errorText = "Could not read Codex account status: " + error
      return false
    }
  }

  function refresh(refreshLoginTokens) {
    if (liveProcess.running) {
      refreshQueued = true
      queuedTokenRefresh = queuedTokenRefresh || refreshLoginTokens === true
      return
    }
    refreshing = true
    liveProcess.command = refreshLoginTokens === true
      ? ["codex-mux", "omarchy"]
      : ["codex-mux", "omarchy", "--no-refresh"]
    liveProcess.running = true
  }

  function openDetails(account) {
    if (!account) return
    detailAccountId = String(account.id || "")
    if (panelFlick) panelFlick.contentY = 0
  }

  function showOverview() {
    detailAccountId = ""
    if (panelFlick) panelFlick.contentY = 0
  }

  function moveSelection(delta) {
    if (accounts.length === 0) return
    selectedIndex = clamp(selectedIndex + delta, 0, accounts.length - 1)
    if (panelFlick && accounts.length > 4)
      panelFlick.contentY = clamp(selectedIndex * Style.space(124), 0,
        Math.max(0, panelFlick.contentHeight - panelFlick.height))
  }

  function launchBalanced() {
    if (bar) bar.run("xdg-terminal-exec codex-lb")
    close()
  }

  Component.onCompleted: {
    cacheProcess.running = true
    initialRefresh.start()
  }

  onOpenedChanged: if (opened) {
    nowMs = Date.now()
    if (panelFlick) panelFlick.contentY = 0
    if (lastLiveRefreshMs === 0 || nowMs - lastLiveRefreshMs > 30000) refresh(false)
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Timer {
    id: initialRefresh
    interval: 75
    repeat: false
    onTriggered: root.refresh(false)
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    running: true
    repeat: true
    onTriggered: root.refresh(false)
  }

  Timer {
    interval: 30000
    running: root.opened
    repeat: true
    onTriggered: root.nowMs = Date.now()
  }

  Process {
    id: cacheProcess
    command: ["codex-mux", "omarchy", "--cached"]
    running: false
    stdout: StdioCollector { id: cacheStdout; waitForEnd: true }
    stderr: StdioCollector { waitForEnd: true }
    onExited: root.applyPayload(cacheStdout.text, false)
  }

  Process {
    id: liveProcess
    running: false
    stdout: StdioCollector { id: liveStdout; waitForEnd: true }
    stderr: StdioCollector { id: liveStderr; waitForEnd: true }

    onExited: function(exitCode) {
      refreshing = false
      var applied = root.applyPayload(liveStdout.text, true)
      if (!applied && String(liveStderr.text || "").trim() !== "")
        errorText = String(liveStderr.text).trim()
      if (refreshQueued) {
        var refreshTokens = queuedTokenRefresh
        refreshQueued = false
        queuedTokenRefresh = false
        root.refresh(refreshTokens)
      }
    }
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refresh(true); return "ok" }
  }

  visible: true
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󱚣"
    active: root.alarming
    tooltipText: "Codex accounts"
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) root.launchBalanced()
      else if (buttonCode === Qt.MiddleButton) root.refresh(true)
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(
      Math.max(Style.space(210), viewLoader.item ? viewLoader.item.implicitHeight : 0),
      Style.space(650))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent

      onMoveRequested: function(dx, dy) {
        if (root.inDetail && dx < 0) root.showOverview()
        else if (!root.inDetail && dy !== 0) root.moveSelection(dy)
        else if (dy !== 0)
          panelFlick.contentY = root.clamp(panelFlick.contentY + dy * Style.space(56), 0,
            Math.max(0, panelFlick.contentHeight - panelFlick.height))
      }
      onActivateRequested: {
        if (root.inDetail) root.refresh(true)
        else if (root.accounts.length > 0) root.openDetails(root.accounts[root.selectedIndex])
      }
      onCloseRequested: {
        if (root.inDetail) root.showOverview()
        else root.close()
      }
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "r" || text === "R") root.refresh(true)
        else if ((text === "h" || text === "H") && root.inDetail) root.showOverview()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: viewLoader.item ? viewLoader.item.implicitHeight : 0
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Loader {
          id: viewLoader
          width: panelFlick.width
          sourceComponent: root.inDetail ? detailView : overviewView
        }
      }
    }
  }

  Component {
    id: overviewView

    Column {
      id: overviewColumn
      width: panelFlick.width
      spacing: Style.space(10)

      PanelHero {
        width: parent.width
        title: "Codex Accounts"
        meta: root.overviewMeta()
        foreground: root.foreground
        fontFamily: root.fontFamily

        iconComponent: Component {
          Text {
            textFormat: Text.PlainText
            text: "󱚣"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.display
          }
        }

        trailingControl: Component {
          PanelActionButton {
            iconText: root.refreshing ? "…" : "󰑐"
            tooltipText: "Refresh accounts"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.refresh(true)
          }
        }
      }

      BorderSurface {
        visible: root.errorText !== ""
        width: parent.width
        implicitHeight: errorMessage.implicitHeight + Style.space(18)
        color: root.alpha(root.urgent, 0.10)
        borderSpec: Border.flat(root.alpha(root.urgent, 0.35), 1)
        radius: Style.cornerRadius

        Text {
          id: errorMessage
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          anchors.leftMargin: Style.space(10)
          anchors.rightMargin: Style.space(10)
          textFormat: Text.PlainText
          text: root.errorText
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
      }

      BorderSurface {
        visible: root.accounts.length === 0
        width: parent.width
        implicitHeight: emptyText.implicitHeight + Style.space(42)
        color: root.cardSurface
        borderSpec: Border.controlSpec("normal", root.foreground, root.accent)
        radius: Style.cornerRadius

        Text {
          id: emptyText
          anchors.centerIn: parent
          width: parent.width - Style.space(30)
          textFormat: Text.PlainText
          text: root.refreshing
            ? "Loading Codex accounts…"
            : "No accounts found. Add one with codex-as add NAME."
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          horizontalAlignment: Text.AlignHCenter
          wrapMode: Text.WordWrap
        }
      }

      Repeater {
        model: root.accounts

        AccountCard {
          required property var modelData
          required property int index
          width: parent.width
          account: modelData
          accountIndex: index
        }
      }

      Text {
        visible: root.accounts.length > 0
        width: parent.width
        textFormat: Text.PlainText
        text: "Select an account for details · right-click the bar icon to launch"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
      }
    }
  }

  Component {
    id: detailView

    Column {
      id: detailColumn
      width: panelFlick.width
      spacing: Style.space(12)
      readonly property var account: root.detailAccount

      Item {
        width: parent.width
        implicitHeight: Math.max(backButton.implicitHeight, refreshButton.implicitHeight)

        PanelActionButton {
          id: backButton
          iconText: "‹"
          tooltipText: "Back to accounts"
          foreground: root.foreground
          fontFamily: root.fontFamily
          fontSize: Style.font.title
          anchors.left: parent.left
          onClicked: root.showOverview()
        }

        PanelActionButton {
          id: refreshButton
          iconText: root.refreshing ? "…" : "󰑐"
          tooltipText: "Refresh account data"
          foreground: root.foreground
          fontFamily: root.fontFamily
          anchors.right: parent.right
          onClicked: root.refresh(true)
        }
      }

      PanelHero {
        width: parent.width
        title: detailColumn.account ? String(detailColumn.account.name || "") : ""
        meta: detailColumn.account ? root.planLabel(detailColumn.account) : ""
        detail: detailColumn.account && detailColumn.account.isDefault === true ? "DEFAULT" : ""
        foreground: root.foreground
        fontFamily: root.fontFamily

        iconComponent: Component {
          Text {
            textFormat: Text.PlainText
            text: "󱚣"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.display
          }
        }
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: detailColumn.account ? String(detailColumn.account.email || "Email unavailable") : ""
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        elide: Text.ElideRight
      }

      Row {
        spacing: Style.space(6)

        StatusPill {
          label: detailColumn.account ? String(detailColumn.account.statusLabel || "") : ""
          tone: detailColumn.account ? root.accountTone(detailColumn.account) : root.dim
        }

        StatusPill {
          visible: detailColumn.account && detailColumn.account.isRecommended === true
          label: "BEST"
          tone: root.accent
        }
      }

      BorderSurface {
        visible: detailColumn.account && String(detailColumn.account.statusDetail || "") !== ""
        width: parent.width
        implicitHeight: detailStatusText.implicitHeight + Style.space(20)
        color: root.alpha(root.accountTone(detailColumn.account), 0.08)
        borderSpec: Border.flat(root.alpha(root.accountTone(detailColumn.account), 0.30), 1)
        radius: Style.cornerRadius

        Text {
          id: detailStatusText
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          anchors.leftMargin: Style.space(10)
          anchors.rightMargin: Style.space(10)
          textFormat: Text.PlainText
          text: detailColumn.account ? String(detailColumn.account.statusDetail || "") : ""
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
      }

      PanelSeparator {
        visible: detailColumn.account && detailColumn.account.limits
          && detailColumn.account.limits.length > 0
        foreground: root.foreground
      }

      PanelSectionHeader {
        visible: detailColumn.account && detailColumn.account.limits
          && detailColumn.account.limits.length > 0
        text: "REMAINING LIMITS"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      Repeater {
        model: detailColumn.account && detailColumn.account.limits ? detailColumn.account.limits : []

        DetailLimitCard {
          required property var modelData
          width: parent.width
          limit: modelData
        }
      }
    }
  }

  component AccountCard: BorderSurface {
    id: accountCard
    property var account: null
    property int accountIndex: -1

    readonly property bool hovered: accountMouse.containsMouse
    readonly property bool selected: accountIndex === root.selectedIndex
    readonly property color tone: root.accountTone(account)

    implicitHeight: cardBody.implicitHeight + Style.space(20)
    color: selected || hovered
      ? Style.hoverFillFor(root.foreground, root.accent)
      : root.cardSurface
    borderSpec: selected || hovered
      ? Border.controlSpec("hover-cursor", root.foreground, root.accent)
      : Border.controlSpec("normal", root.foreground, root.accent)
    radius: Style.cornerRadius

    Behavior on color { ColorAnimation { duration: 100 } }

    Column {
      id: cardBody
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(11)
      anchors.rightMargin: Style.space(11)
      spacing: Style.space(5)

      Item {
        width: parent.width
        implicitHeight: Math.max(accountName.implicitHeight, badges.implicitHeight)

        Text {
          id: accountName
          anchors.left: parent.left
          anchors.right: badges.left
          anchors.rightMargin: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: accountCard.account ? String(accountCard.account.name || "") : ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
          elide: Text.ElideRight
        }

        Row {
          id: badges
          anchors.right: chevron.left
          anchors.rightMargin: Style.space(5)
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(5)

          StatusPill {
            visible: accountCard.account && accountCard.account.isDefault === true
            label: "★"
            tone: root.accent
          }

          StatusPill {
            visible: accountCard.account && accountCard.account.isRecommended === true
            label: "BEST"
            tone: root.accent
          }

          StatusPill {
            label: accountCard.account ? String(accountCard.account.statusLabel || "") : ""
            tone: accountCard.tone
          }

          StatusPill {
            label: root.planLabel(accountCard.account)
            tone: root.foreground
          }
        }

        Text {
          id: chevron
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: "›"
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
        }
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: accountCard.account && String(accountCard.account.email || "") !== ""
          ? String(accountCard.account.email)
          : "Email unavailable"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
      }

      Item { width: 1; height: Style.space(1) }

      Repeater {
        model: accountCard.account && accountCard.account.limits ? accountCard.account.limits : []

        CompactLimit {
          required property var modelData
          width: cardBody.width
          limit: modelData
        }
      }

      Item {
        visible: accountCard.account && (!accountCard.account.limits || accountCard.account.limits.length === 0)
        width: parent.width
        implicitHeight: unavailableLabel.implicitHeight

        Text {
          id: unavailableLabel
          anchors.left: parent.left
          anchors.right: parent.right
          textFormat: Text.PlainText
          text: accountCard.account ? String(accountCard.account.statusLabel || "Unavailable") : ""
          color: accountCard.tone
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          font.bold: true
          elide: Text.ElideRight
        }
      }
    }

    MouseArea {
      id: accountMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.selectedIndex = accountCard.accountIndex
      onClicked: root.openDetails(accountCard.account)
    }
  }

  component CompactLimit: Item {
    id: compactLimit
    property var limit: null
    readonly property real remaining: limit ? Number(limit.remainingPercent) : 0

    implicitHeight: Style.space(27)

    Text {
      id: compactLabel
      anchors.left: parent.left
      anchors.top: parent.top
      textFormat: Text.PlainText
      text: compactLimit.limit ? String(compactLimit.limit.label || "Limit") : ""
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
    }

    Text {
      anchors.left: compactLabel.right
      anchors.right: parent.right
      anchors.leftMargin: Style.space(8)
      anchors.top: parent.top
      textFormat: Text.PlainText
      text: Math.round(compactLimit.remaining) + "% left · " + root.resetLabel(compactLimit.limit, false)
      color: compactLimit.remaining <= 20 ? root.urgent : root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: compactLimit.remaining <= 20
      horizontalAlignment: Text.AlignRight
      elide: Text.ElideRight
    }

    RemainingMeter {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      value: compactLimit.remaining / 100
      tone: root.limitTone(compactLimit.limit)
    }
  }

  component DetailLimitCard: BorderSurface {
    id: detailLimit
    property var limit: null
    readonly property real remaining: limit ? Number(limit.remainingPercent) : 0

    implicitHeight: detailLimitBody.implicitHeight + Style.space(22)
    color: root.cardSurface
    borderSpec: Border.controlSpec("normal", root.foreground, root.accent)
    radius: Style.cornerRadius

    Column {
      id: detailLimitBody
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(12)
      anchors.rightMargin: Style.space(12)
      spacing: Style.space(8)

      Item {
        width: parent.width
        implicitHeight: Math.max(detailLimitName.implicitHeight, detailLimitValue.implicitHeight)

        Text {
          id: detailLimitName
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: detailLimit.limit ? String(detailLimit.limit.label || "Limit") : ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: true
        }

        Text {
          id: detailLimitValue
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: Math.round(detailLimit.remaining) + "% left"
          color: detailLimit.remaining <= 20 ? root.urgent : root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
        }
      }

      RemainingMeter {
        width: parent.width
        value: detailLimit.remaining / 100
        tone: root.limitTone(detailLimit.limit)
        thickness: Style.space(6)
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: root.resetLabel(detailLimit.limit, true)
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }

  component RemainingMeter: Item {
    id: meter
    property real value: 0
    property color tone: root.accent
    property real thickness: Math.max(Style.space(4), Math.round(Style.spacing.controlHeight * 0.13))

    implicitHeight: thickness

    Rectangle {
      id: meterTrack
      anchors.fill: parent
      radius: height / 2
      color: root.track
    }

    Rectangle {
      anchors.left: meterTrack.left
      anchors.verticalCenter: meterTrack.verticalCenter
      width: meterTrack.width * root.clamp(meter.value, 0, 1)
      height: meterTrack.height
      radius: meterTrack.radius
      color: meter.tone

      Behavior on width {
        NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
      }
    }
  }

  component StatusPill: BorderSurface {
    id: pill
    property string label: ""
    property color tone: root.foreground

    implicitWidth: pillText.implicitWidth + Style.space(9)
    implicitHeight: pillText.implicitHeight + Style.space(4)
    color: root.alpha(tone, 0.08)
    borderSpec: Border.flat(root.alpha(tone, 0.40), 1)
    radius: Style.cornerRadius

    Text {
      id: pillText
      anchors.centerIn: parent
      textFormat: Text.PlainText
      text: pill.label.toUpperCase()
      color: pill.tone
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      font.letterSpacing: 0.6
    }
  }
}
