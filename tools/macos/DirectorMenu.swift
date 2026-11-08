// Director 메뉴 막대 앱: 서버(LaunchAgent) 상태 표시, 켜기·끄기·다시 시작, 로그 보기.
// 빌드·설치: tools/macos/install.sh (Xcode 프로젝트 없이 swiftc 한 번).
import AppKit

let serverLabel = "local.ai-dev-director.server"
let dashboardURL = URL(string: "http://127.0.0.1:8081/")!
let statusURL = URL(string: "http://127.0.0.1:8081/status")!
let root = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : (NSHomeDirectory() as NSString).appendingPathComponent("work/ai-dev-director")
let logPath = (root as NSString).appendingPathComponent("logs/companion_server.log")
let agentPlist = (NSHomeDirectory() as NSString).appendingPathComponent("Library/LaunchAgents/\(serverLabel).plist")
let domain = "gui/\(getuid())"

enum ServerState: Equatable {
    case running(pid: Int32, memoryMB: Int)
    case notResponding(pid: Int32)
    case stopped
    case notInstalled
}

@discardableResult
func run(_ path: String, _ args: [String]) -> (code: Int32, out: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: path)
    p.arguments = args
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = pipe
    do { try p.run() } catch { return (-1, "\(error)") }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return (p.terminationStatus, String(decoding: data, as: UTF8.self))
}

func launchdPID() -> Int32? {
    let r = run("/bin/launchctl", ["print", "\(domain)/\(serverLabel)"])
    guard r.code == 0 else { return nil }
    for line in r.out.split(separator: "\n") {
        let t = line.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("pid = "), let v = Int32(t.dropFirst(6)) { return v }
    }
    return 0          // loaded, not running
}

func memoryMB(_ pid: Int32) -> Int {
    let r = run("/bin/ps", ["-o", "rss=", "-p", "\(pid)"])
    return (Int(r.out.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0) / 1024
}

func responds() -> Bool {
    var req = URLRequest(url: statusURL)
    req.timeoutInterval = 3
    let sem = DispatchSemaphore(value: 0)
    var ok = false
    URLSession.shared.dataTask(with: req) { _, resp, _ in
        ok = ((resp as? HTTPURLResponse)?.statusCode ?? 0) == 200
        sem.signal()
    }.resume()
    _ = sem.wait(timeout: .now() + 4)
    return ok
}

func currentState() -> ServerState {
    guard FileManager.default.fileExists(atPath: agentPlist) else { return .notInstalled }
    guard let pid = launchdPID(), pid > 0 else { return .stopped }
    return responds() ? .running(pid: pid, memoryMB: memoryMB(pid)) : .notResponding(pid: pid)
}

func lastLogLines(_ n: Int) -> [String] {
    guard let h = FileHandle(forReadingAtPath: logPath) else { return ["(로그 파일이 아직 없습니다)"] }
    defer { try? h.close() }
    let size = (try? h.seekToEnd()) ?? 0
    try? h.seek(toOffset: size > 16384 ? size - 16384 : 0)
    let text = String(decoding: h.readDataToEndOfFile(), as: UTF8.self)
    let lines = text.split(separator: "\n").map(String.init)
    return Array(lines.suffix(n))
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let menu = NSMenu()
    var state: ServerState = .stopped
    var timer: Timer?
    let queue = DispatchQueue(label: "director.menu.poll")

    func applicationDidFinishLaunching(_ n: Notification) {
        menu.delegate = self
        item.menu = menu
        item.button?.toolTip = "AI DEV Director"
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in self?.refresh() }
    }

    func refresh() {
        queue.async {
            let s = currentState()
            DispatchQueue.main.async { self.apply(s) }
        }
    }

    func apply(_ s: ServerState) {
        state = s
        let (symbol, color): (String, NSColor) = {
            switch s {
            case .running: return ("server.rack", .systemGreen)
            case .notResponding: return ("exclamationmark.triangle.fill", .systemOrange)
            case .stopped, .notInstalled: return ("server.rack", .tertiaryLabelColor)
            }
        }()
        let cfg = NSImage.SymbolConfiguration(pointSize: 14, weight: .regular)
            .applying(.init(paletteColors: [color]))
        item.button?.image = NSImage(systemSymbolName: symbol, accessibilityDescription: "Director")?
            .withSymbolConfiguration(cfg)
    }

    var stateText: String {
        switch state {
        case .running(let pid, let mb): return "● 실행 중 · 응답함 (PID \(pid), 메모리 \(mb)MB)"
        case .notResponding(let pid): return "▲ 실행 중이지만 응답 없음 (PID \(pid))"
        case .stopped: return "○ 꺼짐"
        case .notInstalled: return "○ 설치 안 됨 (tools/macos/install.sh)"
        }
    }

    // 메뉴를 열 때마다 상태와 최근 로그를 다시 읽는다.
    func menuNeedsUpdate(_ menu: NSMenu) {
        refresh()                       // 최신 상태는 뒤에서 다시 읽고, 메뉴는 마지막 상태로 바로 연다
        menu.removeAllItems()
        let header = NSMenuItem(title: "AI DEV Director", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)
        let st = NSMenuItem(title: stateText, action: nil, keyEquivalent: "")
        st.isEnabled = false
        menu.addItem(st)
        menu.addItem(.separator())

        let isUp: Bool = { if case .stopped = state { return false }; if case .notInstalled = state { return false }; return true }()
        add("대시보드 열기", #selector(openDashboard), enabled: isUp, key: "d")
        menu.addItem(.separator())
        add("켜기", #selector(start), enabled: state == .stopped)
        add("끄기", #selector(stop), enabled: isUp)
        add("다시 시작", #selector(restart), enabled: isUp, key: "r")
        menu.addItem(.separator())

        let recent = NSMenuItem(title: "최근 로그", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        for line in lastLogLines(20) {
            let short = line.count > 110 ? String(line.prefix(110)) + "…" : line
            let li = NSMenuItem(title: short, action: nil, keyEquivalent: "")
            li.isEnabled = false
            sub.addItem(li)
        }
        recent.submenu = sub
        menu.addItem(recent)
        add("로그 보기 (콘솔)", #selector(openLog), key: "l")
        add("로그 폴더 열기", #selector(openLogFolder))
        menu.addItem(.separator())
        add("메뉴 막대 앱 종료", #selector(quit), key: "q")
    }

    private func add(_ title: String, _ sel: Selector, enabled: Bool = true, key: String = "") {
        let mi = NSMenuItem(title: title, action: enabled ? sel : nil, keyEquivalent: key)
        mi.target = self
        mi.isEnabled = enabled
        menu.addItem(mi)
    }

    private func confirm(_ what: String) -> Bool {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = "Director 서버를 \(what)할까요?"
        a.informativeText = "진행 중인 미션이 있으면 지금 단계가 중단됩니다. 다음에 켤 때 이어서 진행하도록 설정되어 있으면 다시 이어집니다."
        a.addButton(withTitle: what)
        a.addButton(withTitle: "취소")
        return a.runModal() == .alertFirstButtonReturn
    }

    @objc func openDashboard() { NSWorkspace.shared.open(dashboardURL) }

    @objc func start() {
        queue.async {
            if launchdPID() == nil { run("/bin/launchctl", ["bootstrap", domain, agentPlist]) }
            run("/bin/launchctl", ["kickstart", "\(domain)/\(serverLabel)"])
            Thread.sleep(forTimeInterval: 3)
            self.refresh()
        }
    }

    @objc func stop() {
        guard confirm("끄기") else { return }
        queue.async {
            run("/bin/launchctl", ["kill", "SIGTERM", "\(domain)/\(serverLabel)"])
            Thread.sleep(forTimeInterval: 2)
            self.refresh()
        }
    }

    @objc func restart() {
        guard confirm("다시 시작") else { return }
        queue.async {
            if launchdPID() == nil { run("/bin/launchctl", ["bootstrap", domain, agentPlist]) }
            run("/bin/launchctl", ["kickstart", "-k", "\(domain)/\(serverLabel)"])
            Thread.sleep(forTimeInterval: 3)
            self.refresh()
        }
    }

    @objc func openLog() {
        if !FileManager.default.fileExists(atPath: logPath) { FileManager.default.createFile(atPath: logPath, contents: nil) }
        run("/usr/bin/open", ["-a", "Console", logPath])
    }

    @objc func openLogFolder() {
        NSWorkspace.shared.open(URL(fileURLWithPath: (logPath as NSString).deletingLastPathComponent))
    }

    @objc func quit() { NSApp.terminate(nil) }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
