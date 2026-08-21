import UIKit
import UserNotifications
import UserNotificationsUI

final class NotificationViewController: UIViewController, UNNotificationContentExtension {
    private let imageView = UIImageView()
    private let keywordLabel = UILabel()
    private let titleLabel = UILabel()
    private let bodyLabel = UILabel()
    private let textStack = UIStackView()
    private let container = UIStackView()
    private var imageWidthConstraint: NSLayoutConstraint?
    private var imageHeightConstraint: NSLayoutConstraint?
    private var imageTask: URLSessionDataTask?
    private let maximumImageBytes = 10 * 1024 * 1024

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground

        imageView.contentMode = .scaleAspectFit
        imageView.clipsToBounds = true
        imageView.layer.cornerRadius = 8
        imageView.backgroundColor = .secondarySystemBackground
        imageView.translatesAutoresizingMaskIntoConstraints = false
        imageView.isAccessibilityElement = true
        imageView.accessibilityIdentifier = "notification.previewMedia"
        imageView.accessibilityLabel = "Notification media preview"

        keywordLabel.font = .preferredFont(forTextStyle: .caption1)
        keywordLabel.textColor = .secondaryLabel
        keywordLabel.numberOfLines = 1

        titleLabel.font = .preferredFont(forTextStyle: .headline)
        titleLabel.numberOfLines = 2

        bodyLabel.font = .preferredFont(forTextStyle: .body)
        bodyLabel.numberOfLines = 6

        textStack.axis = .vertical
        textStack.spacing = 4
        textStack.translatesAutoresizingMaskIntoConstraints = false
        [keywordLabel, titleLabel, bodyLabel].forEach(textStack.addArrangedSubview)

        container.axis = .vertical
        container.spacing = 12
        container.alignment = .fill
        container.translatesAutoresizingMaskIntoConstraints = false
        [imageView, textStack].forEach(container.addArrangedSubview)
        view.addSubview(container)

        let imageWidthConstraint = imageView.widthAnchor.constraint(equalToConstant: 1)
        let imageHeightConstraint = imageView.heightAnchor.constraint(equalToConstant: 180)
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

        let keyword = stringValue(userInfo["watch_term_keyword"]).map { base in
            guard let moreCount = moreCount(from: userInfo) else { return base }
            return "\(base) ほか\(moreCount)件"
        }
        let title = firstNonEmpty(
            stringValue(userInfo["item_title"]),
            stringValue(previewItem?["title"])
        )
        let body = firstNonEmpty(
            stringValue(userInfo["item_content_text"]),
            stringValue(previewItem?["content_text"])
        )
        keywordLabel.text = keyword
        titleLabel.text = title
        bodyLabel.text = body == title ? nil : body
        imageTask?.cancel()
        imageTask = nil
        imageView.image = image(from: content.attachments.first)
        imageView.isHidden = imageView.image == nil
        keywordLabel.isHidden = keywordLabel.text?.isEmpty ?? true
        titleLabel.isHidden = titleLabel.text?.isEmpty ?? true
        bodyLabel.isHidden = bodyLabel.text?.isEmpty ?? true
        updateLayout(for: view.bounds.width)
        updatePreferredContentSize()

        if imageView.image == nil, let thumbnailURL = thumbnailURL(from: userInfo) {
            loadImage(from: thumbnailURL)
        }
    }

    private func updateLayout(for width: CGFloat) {
        guard width > 0 else { return }
        let availableWidth = max(1, width - 28)
        imageWidthConstraint?.constant = availableWidth
        if let image = imageView.image, image.size.width > 0, image.size.height > 0 {
            let aspectHeight = availableWidth * image.size.height / image.size.width
            imageHeightConstraint?.constant = min(240, max(140, aspectHeight))
        } else {
            imageHeightConstraint?.constant = min(200, max(140, availableWidth * 9 / 16))
        }
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

    private func moreCount(from userInfo: [AnyHashable: Any]) -> Int? {
        guard let count = stringValue(userInfo["new_count"]),
              let numericCount = Int(count),
              numericCount > 1
        else { return nil }
        return numericCount - 1
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

    private func thumbnailURL(from userInfo: [AnyHashable: Any]) -> URL? {
        let previewItem = dictionaryValue(userInfo["preview_item"])
        let rawURL = firstNonEmpty(
            stringValue(userInfo["thumbnail_url"]),
            stringValue(previewItem?["thumbnail_url"])
        )
        guard let rawURL,
              let url = URL(string: rawURL),
              ["http", "https"].contains(url.scheme?.lowercased())
        else { return nil }
        return url
    }

    private func loadImage(from url: URL) {
        var request = URLRequest(url: url)
        request.timeoutInterval = 8
        imageTask = URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
            guard let self,
                  let data,
                  data.count <= self.maximumImageBytes,
                  let http = response as? HTTPURLResponse,
                  (200...299).contains(http.statusCode),
                  http.mimeType?.lowercased().hasPrefix("image/") == true,
                  let image = UIImage(data: data)
            else { return }

            DispatchQueue.main.async {
                self.imageView.image = image
                self.imageView.isHidden = false
                self.updateLayout(for: self.view.bounds.width)
                self.updatePreferredContentSize()
            }
        }
        imageTask?.resume()
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
