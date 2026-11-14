// SoundShredder for Mac: a native window for the local SoundShredder tool.
//
// Opening the app starts SoundShredder's Python server (127.0.0.1 only) from the
// SoundShredder folder, shows its interface in a WebKit window, saves downloads to
// ~/Downloads, and stops the server when the app quits. If a SoundShredder server is
// already running (for example from "Start SoundShredder.command"), the app uses it
// and leaves it running. Build and install with: bash mac-app/build_app.sh

import Cocoa
import WebKit

private let rootDefaultsKey = "SoundShredderRoot"
private let background = NSColor(srgbRed: 0.04, green: 0.06, blue: 0.07, alpha: 1)
private let privacySettingsURL = URL(
    string: "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders")!

enum PortState { case soundShredder, free, busy }

/// True when 127.0.0.1:port can be bound (same SO_REUSEADDR rule uvicorn uses).
func portIsFree(_ port: Int) -> Bool {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    guard fd >= 0 else { return false }
    defer { close(fd) }
    var yes: Int32 = 1
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))
    var address = sockaddr_in()
    address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    address.sin_family = sa_family_t(AF_INET)
    address.sin_port = in_port_t(UInt16(port).bigEndian)
    address.sin_addr.s_addr = inet_addr("127.0.0.1")
    let result = withUnsafePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
    }
    return result == 0
}

func escapeHTML(_ text: String) -> String {
    text.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        .replacingOccurrences(of: ">", with: "&gt;")
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate,
    WKDownloadDelegate
{
    private var window: NSWindow!
    private var webView: WKWebView!
    private var root: URL?
    private var server: Process?
    private var waiting: Task<Void, Never>?
    private var ownsServer = false
    private var quitting = false
    private var quitConfirmed = false
    private var port = 7860
    private var downloads: [ObjectIdentifier: URL] = [:]
    private let logURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/SoundShredder/server.log")
    private var appURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }

    // MARK: Launch

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        if #available(macOS 14, *) { NSApp.activate() } else { NSApp.activate(ignoringOtherApps: true) }
        start()
    }

    private func start() {
        showStarting()
        if let process = server, process.isRunning {  // "Try Again" while our engine is still starting
            watch(process)
            return
        }
        guard let root = resolveRoot() else { NSApp.terminate(nil); return }
        self.root = root
        Task { await connectOrLaunch() }
    }

    private func showStarting() {
        showStatus("Starting SoundShredder…", "Loading the separation engine. This takes a few seconds.", busy: true)
    }

    private func isSoundShredderFolder(_ url: URL) -> Bool {
        FileManager.default.fileExists(atPath: url.appendingPathComponent("app.py").path)
            && FileManager.default.isExecutableFile(atPath: url.appendingPathComponent(".venv/bin/python").path)
    }

    /// The folder saved by "Choose Folder…", else the one recorded when the app was built.
    private func resolveRoot() -> URL? {
        var paths: [String] = []
        if let saved = UserDefaults.standard.string(forKey: rootDefaultsKey) { paths.append(saved) }
        if let built = Bundle.main.object(forInfoDictionaryKey: rootDefaultsKey) as? String, !built.isEmpty {
            paths.append(built)
        }
        while true {
            if let found = paths.map({ URL(fileURLWithPath: $0, isDirectory: true) }).first(where: isSoundShredderFolder) {
                return found
            }
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "SoundShredder folder not found"
            alert.informativeText = "Expected it at:\n\(paths.last ?? "(not set)")\n\nConnect the drive that holds it, "
                + "or choose the SoundShredder folder. It must contain app.py and the .venv folder that "
                + "\"Start SoundShredder.command\" creates on its first run."
            alert.addButton(withTitle: "Try Again")
            alert.addButton(withTitle: "Choose Folder…")
            alert.addButton(withTitle: "Quit")
            switch alert.runModal() {
            case .alertFirstButtonReturn:
                continue
            case .alertSecondButtonReturn:
                let panel = NSOpenPanel()
                panel.canChooseDirectories = true
                panel.canChooseFiles = false
                panel.message = "Choose the SoundShredder folder"
                if panel.runModal() == .OK, let url = panel.url {
                    UserDefaults.standard.set(url.path, forKey: rootDefaultsKey)
                    paths.insert(url.path, at: 0)
                }
            default:
                return nil
            }
        }
    }

    private func probe(_ port: Int, checkBind: Bool) async -> PortState {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/system")!)
        request.timeoutInterval = 15
        do {
            let (data, _) = try await URLSession.shared.data(for: request)
            let info = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            return info?["name"] as? String == "SoundShredder" ? .soundShredder : .busy
        } catch {
            return checkBind && portIsFree(port) ? .free : .busy
        }
    }

    private func connectOrLaunch() async {
        guard let root else { return }
        for candidate in 7860...7870 {
            switch await probe(candidate, checkBind: true) {
            case .soundShredder:
                port = candidate
                ownsServer = false
                loadApp()
                return
            case .free:
                port = candidate
                let began = Date()
                guard await canOpenFolder(root) else {
                    fail(
                        "SoundShredder can't open its folder",
                        "macOS blocked access to:\n\(root.path)\n\nIn System Settings › Privacy & Security › "
                            + "Files & Folders, open SoundShredder and turn on Removable Volumes (or the folder "
                            + "that holds SoundShredder), then click Try Again.",
                        privacyButton: true)
                    return
                }
                if Date().timeIntervalSince(began) > 1 { showStarting() }  // replace the permission notice
                launchServer()
                return
            case .busy:
                continue
            }
        }
        fail("No free port", "Ports 7860–7870 are all used by other programs.")
    }

    /// Opens app.py off the main thread. For a folder on an external drive (or in Desktop, Documents
    /// or Downloads), macOS holds this first read until the user answers its privacy prompt, and the
    /// answer also covers the engine, because macOS attributes the engine's reads to this app. Reading
    /// here, instead of leaving the first read to the engine, lets the window explain the wait and
    /// report a refusal.
    private func canOpenFolder(_ root: URL) async -> Bool {
        let notice = Task {
            try await Task.sleep(nanoseconds: 1_000_000_000)
            guard !Task.isCancelled else { return }
            showStatus(
                "Allow access to the SoundShredder folder",
                "macOS is asking whether SoundShredder may access files on the drive that holds it. "
                    + "Click Allow. The engine starts right after.", busy: true)
        }
        defer { notice.cancel() }
        let file = root.appendingPathComponent("app.py").path
        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let handle = FileHandle(forReadingAtPath: file)
                try? handle?.close()
                continuation.resume(returning: handle != nil)
            }
        }
    }

    private func launchServer() {
        guard let root else { return }
        let process = Process()
        process.executableURL = root.appendingPathComponent(".venv/bin/python")
        process.arguments = [root.appendingPathComponent("app.py").path, "--no-browser", "--port", String(port)]
        process.currentDirectoryURL = root
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment
        try? FileManager.default.createDirectory(
            at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(
            atPath: logURL.path, contents: Data("SoundShredder app: server log, started \(Date())\n".utf8))
        if let log = try? FileHandle(forWritingTo: logURL) {
            _ = try? log.seekToEnd()
            process.standardOutput = log
            process.standardError = log
        }
        process.standardInput = FileHandle.nullDevice
        process.terminationHandler = { [weak self] finished in
            let status = finished.terminationStatus
            Task { @MainActor in self?.serverExited(finished, status: status) }
        }
        do {
            try process.run()
        } catch {
            fail("Could not start SoundShredder", error.localizedDescription)
            return
        }
        server = process
        ownsServer = true
        watch(process)
    }

    private func watch(_ process: Process) {
        waiting?.cancel()
        waiting = Task { await waitUntilReady(process) }
    }

    /// Polls until the engine answers. There is no deadline while the engine runs: a long wait gets
    /// a notice instead, and serverExited reports an exit.
    private func waitUntilReady(_ process: Process) async {
        let began = Date()
        var noticeShown = false
        while !Task.isCancelled, process === server, process.isRunning {
            if await probe(port, checkBind: false) == .soundShredder {
                if !Task.isCancelled { loadApp() }
                return
            }
            if !noticeShown, Date().timeIntervalSince(began) > 90 {
                noticeShown = true
                showStatus(
                    "SoundShredder is still starting…",
                    "This is taking longer than usual. Choose File › Show Server Log to see what the engine "
                        + "is doing, or quit and open SoundShredder again.", busy: true)
            }
            try? await Task.sleep(nanoseconds: 400_000_000)
        }
    }

    private func serverExited(_ process: Process, status: Int32) {
        guard process === server else { return }
        server = nil
        ownsServer = false
        if quitting { return }
        Task {
            // Another SoundShredder took the port while ours was starting: use that one.
            if await probe(port, checkBind: false) == .soundShredder { loadApp(); return }
            let log = (try? String(contentsOf: logURL, encoding: .utf8)) ?? ""
            fail("SoundShredder stopped", "The engine exited (code \(status)).\n\n" + String(log.suffix(1500)))
        }
    }

    private func loadApp() {
        webView.load(URLRequest(url: appURL))
    }

    // MARK: Window and status pages

    private func buildWindow() {
        let configuration = WKWebViewConfiguration()
        configuration.mediaTypesRequiringUserActionForPlayback = []
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsMagnification = true
        webView.underPageBackgroundColor = background
        if #available(macOS 13.3, *) { webView.isInspectable = true }

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "SoundShredder"
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = background
        window.minSize = NSSize(width: 760, height: 560)
        window.contentView = webView
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("SoundShredderWindow")
        window.makeKeyAndOrderFront(nil)
    }

    private func showStatus(_ title: String, _ detail: String, busy: Bool) {
        let spinner = busy ? "<div class=\"spin\"></div>" : ""
        let html = """
            <!doctype html><html><head><meta charset="utf-8"><style>
            :root{color-scheme:dark}
            body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
              background:#0a0f11;color:#e7f5f1;font:15px -apple-system,BlinkMacSystemFont,sans-serif}
            .box{max-width:620px;padding:32px;text-align:center}
            .spin{width:34px;height:34px;margin:0 auto 20px;border:3px solid #1c3833;border-top-color:#7de6cf;
              border-radius:50%;animation:r .9s linear infinite}
            @keyframes r{to{transform:rotate(360deg)}}
            h1{margin:0 0 10px;font-size:20px;font-weight:600}
            p{margin:0;color:#9db5af;line-height:1.55;white-space:pre-wrap;text-align:\(busy ? "center" : "left")}
            </style></head><body><div class="box">\(spinner)<h1>\(escapeHTML(title))</h1>
            <p>\(escapeHTML(detail))</p></div></body></html>
            """
        webView.loadHTMLString(html, baseURL: nil)
    }

    private func fail(_ title: String, _ detail: String, privacyButton: Bool = false) {
        guard !quitting else { return }
        showStatus(title, detail, busy: false)
        guard window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = title
        alert.informativeText = String(detail.prefix(600))
        alert.addButton(withTitle: "Try Again")
        if privacyButton { alert.addButton(withTitle: "Open Privacy Settings") }
        alert.addButton(withTitle: "Quit")
        alert.beginSheetModal(for: window) { response in
            switch response {
            case .alertFirstButtonReturn:
                self.start()
            case .alertSecondButtonReturn where privacyButton:
                NSWorkspace.shared.open(privacySettingsURL)
                DispatchQueue.main.async { self.fail(title, detail, privacyButton: true) }  // keep Try Again at hand
            default:
                NSApp.terminate(nil)
            }
        }
    }

    // MARK: Menus

    private func buildMenu() {
        let menu = NSMenu()
        func item(
            _ title: String, _ action: Selector?, _ key: String = "", _ modifiers: NSEvent.ModifierFlags = .command,
            target: AnyObject? = nil
        ) -> NSMenuItem {
            let entry = NSMenuItem(title: title, action: action, keyEquivalent: key)
            entry.keyEquivalentModifierMask = modifiers
            entry.target = target
            return entry
        }
        func submenu(_ title: String, _ items: [NSMenuItem]) -> NSMenu {
            let parent = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let child = NSMenu(title: title)
            items.forEach(child.addItem)
            parent.submenu = child
            menu.addItem(parent)
            return child
        }
        _ = submenu("SoundShredder", [
            item("About SoundShredder", #selector(NSApplication.orderFrontStandardAboutPanel(_:))),
            .separator(),
            item("Hide SoundShredder", #selector(NSApplication.hide(_:)), "h"),
            item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            item("Show All", #selector(NSApplication.unhideAllApplications(_:))),
            .separator(),
            item("Quit SoundShredder", #selector(NSApplication.terminate(_:)), "q"),
        ])
        _ = submenu("File", [
            item("Open in Browser", #selector(openInBrowser), "o", [.command, .shift], target: self),
            item("Show Output Folder", #selector(showOutputFolder), target: self),
            item("Show Server Log", #selector(showLog), target: self),
            .separator(),
            item("Close Window", #selector(NSWindow.performClose(_:)), "w"),
        ])
        _ = submenu("Edit", [
            item("Undo", Selector(("undo:")), "z"),
            item("Redo", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x"),
            item("Copy", #selector(NSText.copy(_:)), "c"),
            item("Paste", #selector(NSText.paste(_:)), "v"),
            item("Select All", #selector(NSText.selectAll(_:)), "a"),
        ])
        _ = submenu("View", [
            item("Reload", #selector(reload), "r", target: self),
            .separator(),
            item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ])
        NSApp.windowsMenu = submenu("Window", [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Zoom", #selector(NSWindow.performZoom(_:))),
        ])
        NSApp.mainMenu = menu
    }

    @objc private func reload() { loadApp() }
    @objc private func openInBrowser() { NSWorkspace.shared.open(appURL) }

    @objc private func showOutputFolder() {
        guard let root else { return }
        let data = root.appendingPathComponent("data", isDirectory: true)
        NSWorkspace.shared.activateFileViewerSelecting([FileManager.default.fileExists(atPath: data.path) ? data : root])
    }

    @objc private func showLog() {
        if FileManager.default.fileExists(atPath: logURL.path) { NSWorkspace.shared.open(logURL) } else { NSSound.beep() }
    }

    // MARK: Quitting stops the server this app started

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func windowShouldClose(_ sender: NSWindow) -> Bool { confirmQuitIfBusy() }

    private func confirmQuitIfBusy() -> Bool {
        if quitConfirmed || !ownsServer || !hasActiveJob() { return true }
        let alert = NSAlert()
        alert.messageText = "A separation is still running"
        alert.informativeText = "Quitting SoundShredder stops it. Your original file is not changed."
        alert.addButton(withTitle: "Quit Anyway")
        alert.addButton(withTitle: "Keep Working")
        quitConfirmed = alert.runModal() == .alertFirstButtonReturn
        return quitConfirmed
    }

    private func hasActiveJob() -> Bool {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/system")!)
        request.timeoutInterval = 3
        let semaphore = DispatchSemaphore(value: 0)
        nonisolated(unsafe) var active = false
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data, let info = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                let jobs = info["active_jobs"] as? [Any]
            {
                active = !jobs.isEmpty
            }
            semaphore.signal()
        }.resume()
        _ = semaphore.wait(timeout: .now() + 4)
        return active
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard confirmQuitIfBusy() else { return .terminateCancel }
        guard ownsServer, let process = server, process.isRunning else { return .terminateNow }
        quitting = true
        showStatus("Stopping SoundShredder…", "", busy: true)
        process.terminate()  // SIGTERM: uvicorn shuts down and cancels any running separation
        DispatchQueue.global().async {
            let deadline = Date().addingTimeInterval(15)
            while process.isRunning && Date() < deadline { usleep(100_000) }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }

    // MARK: Navigation: local pages stay here, everything else opens in the default browser

    private func isLocal(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else { return true }
        if ["about", "data", "blob"].contains(scheme) { return true }
        return (scheme == "http" || scheme == "https") && (url.host == "127.0.0.1" || url.host == "localhost")
    }

    func webView(
        _ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void
    ) {
        if navigationAction.shouldPerformDownload { decisionHandler(.download); return }
        guard let url = navigationAction.request.url else { decisionHandler(.allow); return }
        if url.isFileURL { decisionHandler(.cancel); return }  // a file dropped outside the drop zone
        if isLocal(url) { decisionHandler(.allow); return }
        NSWorkspace.shared.open(url)
        decisionHandler(.cancel)
    }

    func webView(
        _ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
        decisionHandler: @escaping @MainActor @Sendable (WKNavigationResponsePolicy) -> Void
    ) {
        let disposition = (navigationResponse.response as? HTTPURLResponse)?
            .value(forHTTPHeaderField: "Content-Disposition") ?? ""
        let download = disposition.lowercased().hasPrefix("attachment") || !navigationResponse.canShowMIMEType
        decisionHandler(download ? .download : .allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let failure = error as NSError
        // Cancelled loads and "frame load interrupted" (a navigation handed to a download) are expected.
        if failure.code == NSURLErrorCancelled || (failure.domain == "WebKitErrorDomain" && failure.code == 102) {
            return
        }
        fail("Can't reach SoundShredder", error.localizedDescription)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { loadApp() }

    // MARK: Downloads go to ~/Downloads and are shown in Finder

    private func uniqueURL(in folder: URL, for name: String) -> URL {
        let clean = name.isEmpty ? "SoundShredder download" : name.replacingOccurrences(of: "/", with: "-")
        let base = (clean as NSString).deletingPathExtension
        let ext = (clean as NSString).pathExtension
        var candidate = folder.appendingPathComponent(clean)
        var number = 1
        while FileManager.default.fileExists(atPath: candidate.path) {
            candidate = folder.appendingPathComponent(ext.isEmpty ? "\(base) (\(number))" : "\(base) (\(number)).\(ext)")
            number += 1
        }
        return candidate
    }

    func download(
        _ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
        completionHandler: @escaping @MainActor @Sendable (URL?) -> Void
    ) {
        let folder = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        let destination = uniqueURL(in: folder, for: suggestedFilename)
        downloads[ObjectIdentifier(download)] = destination
        completionHandler(destination)
    }

    func downloadDidFinish(_ download: WKDownload) {
        guard let url = downloads.removeValue(forKey: ObjectIdentifier(download)) else { return }
        DistributedNotificationCenter.default().post(name: .init("com.apple.DownloadFileFinished"), object: url.path)
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        downloads.removeValue(forKey: ObjectIdentifier(download))
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Download failed"
        alert.informativeText = error.localizedDescription
            + "\n\nIf macOS asked about your Downloads folder and access was refused, allow it in System "
            + "Settings › Privacy & Security › Files & Folders. Finished tracks are also kept in the "
            + "SoundShredder folder (File › Show Output Folder)."
        alert.beginSheetModal(for: window)
    }

    // MARK: File picker, alerts and confirmations used by the page

    func webView(
        _ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping @MainActor @Sendable ([URL]?) -> Void
    ) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.message = "Choose an audio or video file"
        panel.beginSheetModal(for: window) { response in completionHandler(response == .OK ? panel.urls : nil) }
    }

    func webView(
        _ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping @MainActor @Sendable () -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = message
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(
        _ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping @MainActor @Sendable (Bool) -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { response in completionHandler(response == .alertFirstButtonReturn) }
    }

    func webView(
        _ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if let url = navigationAction.request.url { NSWorkspace.shared.open(url) }  // target="_blank" links
        return nil
    }
}

MainActor.assumeIsolated {  // top-level code runs on the main thread
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    app.run()
}
