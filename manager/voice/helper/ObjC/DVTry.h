#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN
/// 跑一段代码并接住 Objective-C 异常，返回异常说明（没出事返回 nil）。
/// AVAudioEngine 在引擎没起来时 play / installTap 会抛 NSException，Swift 自己接不住，会直接让 DeckVoice 崩溃（11-07 03:56）。
FOUNDATION_EXPORT NSString * _Nullable DVTry(NS_NOESCAPE void (^block)(void));
NS_ASSUME_NONNULL_END
