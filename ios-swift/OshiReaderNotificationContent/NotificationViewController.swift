import UIKit
import UserNotifications
import UserNotificationsUI

final class NotificationViewController: UIViewController, UNNotificationContentExtension {
    private let imageView = UIImageView()
    private let titleLabel = UILabel()
    private let bodyLabel = UILabel()
    private let metaLabel = UILabel()
    private let textStack = UIStackView()
    private let container = UIStackView()
    private var imageWidthConstraint: NSLayoutConstraint?
    private var imageHeightConstraint: NSLayoutConstraint?
    private var isCompactLayout = false

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground

        imageView.contentMode = .scaleAspectFill
        imageView.clipsToBounds = true
        imageView.layer.cornerRadius = 8
        imageView.backgroundColor = .secondarySystemBackground
        imageView.translatesAutoresizingMaskIntoConstraints = false

        titleLabel.font = .preferredFont(forTextStyle: .headline)
        titleLabel.numberOfLines = 2

        bodyLabel.font = .preferredFont(forTextStyle: .body)
        bodyLabel.numberOfLines = 6

        metaLabel.font = .preferredFont(forTextStyle: .caption1)
        metaLabel.textColor = .tertiaryLabel
        metaLabel.numberOfLines = 1

        textStack.axis = .vertical
        textStack.spacing = 4
        textStack.translatesAutoresizingMaskIntoConstraints = false
        [titleLabel, bodyLabel, metaLabel].forEach(textStack.addArrangedSubview)

        container.axis = .horizontal
        container.spacing = 12
        container.alignment = .top
        container.translatesAutoresizingMaskIntoConstraints = false
        [imageView, textStack].forEach(container.addArrangedSubview)
        view.addSubview(container)

        let imageWidthConstraint = imageView.widthAnchor.constraint(equalToConstant: 92)
        let imageHeightConstraint = imageView.heightAnchor.constraint(equalToConstant: 92)
        self.imageWidthConstraint = imageWidthConstraint
        self.imageHeightConstraint = imageHeightConstraint

        NSLayoutConstraint.activate([
            container.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 14),
            container.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -14),
            container.topAnchor.constraint(equalTo: view.topAnchor, constant: 14),
            container.bottomAnchor.constraint(lessThanOrEqualTo: view.bottomAnchor, constant: -14),
            imageWidthConstraint,
            imageHeightConstraint,
        ])
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        updateLayout(for: view.bounds.width)
    }

    func didReceive(_ notification: UNNotification) {
        let content = notification.request.content
        let userInfo = content.userInfo
        let previewItem = dictionaryValue(userInfo["preview_item"])

        let title = firstNonEmpty(
            stringValue(userInfo["item_title"]),
            stringValue(previewItem?["title"]),
            content.title,
            stringValue(userInfo["watch_term_keyword"]),
            "OshiReader"
        )
        let body = firstNonEmpty(
            stringValue(userInfo["item_content_text"]),
            stringValue(previewItem?["content_text"]),
            content.body,
            stringValue(userInfo["item_url"]),
            stringValue(previewItem?["url"])
        )
        titleLabel.text = title
        bodyLabel.text = body == title ? nil : body
        metaLabel.text = metaText(from: content.userInfo)
        imageView.image = image(from: content.attachments.first)
        imageView.isHidden = imageView.image == nil
        bodyLabel.isHidden = bodyLabel.text?.isEmpty ?? true
        metaLabel.isHidden = metaLabel.text?.isEmpty ?? true
        updateLayout(for: view.bounds.width)
        updatePreferredContentSize()
    }

    private func updateLayout(for width: CGFloat) {
        let compact = width > 0 && width < 360
        guard compact != isCompactLayout else { return }
        isCompactLayout = compact

        container.axis = compact ? .vertical : .horizontal
        container.alignment = compact ? .fill : .top
        imageView.contentMode = compact ? .scaleAspectFit : .scaleAspectFill
        imageWidthConstraint?.constant = compact ? max(1, width - 28) : 92
        imageHeightConstraint?.constant = compact ? 140 : 92
    }

    private func updatePreferredContentSize() {
        view.setNeedsLayout()
        view.layoutIfNeeded()

        let width = max(view.bounds.width, 1)
        let fittingSize = view.systemLayoutSizeFitting(
            CGSize(width: width, height: UIView.layoutFittingCompressedSize.height),
            withHorizontalFittingPriority: .required,
            verticalFittingPriority: .fittingSizeLevel
        )
        preferredContentSize = CGSize(
            width: width,
            height: min(max(fittingSize.height, 100), 360)
        )
    }

    private func metaText(from userInfo: [AnyHashable: Any]) -> String? {
        guard let count = stringValue(userInfo["new_count"]),
              let numericCount = Int(count),
              numericCount > 1
        else { return nil }
        return "ほか\(numericCount - 1)件"
    }

    private func image(from attachment: UNNotificationAttachment?) -> UIImage? {
        guard let attachment else { return nil }
        let didStart = attachment.url.startAccessingSecurityScopedResource()
        defer {
            if didStart {
                attachment.url.stopAccessingSecurityScopedResource()
            }
        }
        guard let data = try? Data(contentsOf: attachment.url) else { return nil }
        return UIImage(data: data)
    }

    private func stringValue(_ value: Any?) -> String? {
        if let text = value as? String, !text.isEmpty {
            return text
        }
        if let number = value as? NSNumber {
            return number.stringValue
        }
        return nil
    }

    private func dictionaryValue(_ value: Any?) -> [String: Any]? {
        if let dictionary = value as? [String: Any] {
            return dictionary
        }
        if let dictionary = value as? NSDictionary {
            return dictionary as? [String: Any]
        }
        return nil
    }

    private func firstNonEmpty(_ values: String?...) -> String? {
        values.first { value in
            guard let value else { return false }
            return !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        } ?? nil
    }
}
