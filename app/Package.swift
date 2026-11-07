// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "FusionWorkbench",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "FusionWorkbench", path: "Sources/FusionWorkbench")
    ]
)
