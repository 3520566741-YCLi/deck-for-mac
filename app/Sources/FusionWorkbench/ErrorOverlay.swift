import AppKit

/// 连不上时盖在网页上的原生提示：说清楚怎么了、下一步做什么，带“重试”。
final class ErrorOverlay {
    var onRetry: (() -> Void)?
    private var view: NSView?

    func show(title: String, detail: String, showRetry: Bool, in parent: NSView) {
        hide()
        let v = NSView(frame: parent.bounds)
        v.autoresizingMask = [.width, .height]
        v.wantsLayer = true
        v.layer?.backgroundColor = NSColor(red: 0.043, green: 0.051, blue: 0.071, alpha: 1).cgColor

        let t = NSTextField(labelWithString: title)
        t.font = .systemFont(ofSize: 24, weight: .semibold)
        t.textColor = NSColor(white: 0.94, alpha: 1)
        let d = NSTextField(wrappingLabelWithString: detail)
        d.font = .systemFont(ofSize: 15)
        d.textColor = NSColor(white: 0.66, alpha: 1)
        d.alignment = .center
        d.preferredMaxLayoutWidth = 520
        let stack = NSStackView(views: [t, d])
        stack.orientation = .vertical
        stack.spacing = 12
        stack.alignment = .centerX
        if showRetry {
            let b = NSButton(title: "重试", target: self, action: #selector(retry))
            b.bezelStyle = .rounded
            b.controlSize = .large
            stack.addArrangedSubview(b)
        }
        stack.translatesAutoresizingMaskIntoConstraints = false
        v.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: v.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: v.centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualToConstant: 560)
        ])
        parent.addSubview(v)
        view = v
    }

    func hide() { view?.removeFromSuperview(); view = nil }

    @objc private func retry() { onRetry?() }
}
