import Foundation

struct IrasutoyaImage: Codable, Identifiable, Hashable {
    var id: String { url }
    let url: String
    let thumb: String
    let title: String
}

extension NetworkManager {

    // MARK: - Google Translate

    func translateToJapanese(_ text: String) async -> String {
        let isJapanese = text.range(of: "\\p{Hiragana}|\\p{Katakana}|\\p{Han}", options: .regularExpression) != nil
        if isJapanese { return text }

        let query = text.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? text
        let urlString = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ja&dt=t&q=\(query)"
        guard let url = URL(string: urlString) else { return text }

        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            if let json = try? JSONSerialization.jsonObject(with: data) as? [Any],
               let firstArray = json.first as? [Any] {
                var translatedText = ""
                for chunk in firstArray {
                    if let chunkArray = chunk as? [Any], let string = chunkArray.first as? String {
                        translatedText += string
                    }
                }
                return translatedText.isEmpty ? text : translatedText
            }
        } catch {
            AppLogger.network.error("Translation failed: \(error.localizedDescription)")
        }
        return text
    }

    // MARK: - Irasutoya

    func getPopularIrasutoya() async throws -> [IrasutoyaImage] {
        let feed1 = "https://www.irasutoya.com/feeds/posts/default?alt=json&max-results=20"
        let categoryUrl = "https://www.irasutoya.com/feeds/posts/default/-/\(URLQueryItem(name: "", value: "人物").value!.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")?alt=json&max-results=15"

        let items1 = try await fetchBloggerFeed(feed1)
        let items2 = (try? await fetchBloggerFeed(categoryUrl)) ?? []

        var combined = [IrasutoyaImage]()
        var seen = Set<String>()
        for item in items1 + items2 where !seen.contains(item.url) && combined.count < 30 {
            seen.insert(item.url)
            combined.append(item)
        }
        return combined
    }

    func searchIrasutoya(query: String) async throws -> [IrasutoyaImage] {
        let jaQuery = await translateToJapanese(query)
        let escaped = jaQuery.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? jaQuery

        let feedUrl1 = "https://www.irasutoya.com/feeds/posts/default?alt=json&q=\(escaped)&max-results=36&start-index=1"
        let feedUrl2 = "https://www.irasutoya.com/feeds/posts/default?alt=json&q=\(escaped)&max-results=36&start-index=37"

        let items1 = (try? await fetchBloggerFeed(feedUrl1)) ?? []
        let items2 = (try? await fetchBloggerFeed(feedUrl2)) ?? []

        var combined = [IrasutoyaImage]()
        var seen = Set<String>()
        for item in items1 + items2 where !seen.contains(item.url) && combined.count < 72 {
            seen.insert(item.url)
            combined.append(item)
        }
        return combined
    }

    private func fetchBloggerFeed(_ urlString: String) async throws -> [IrasutoyaImage] {
        guard let url = URL(string: urlString) else { return [] }
        var request = URLRequest(url: url)
        request.setValue("Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1", forHTTPHeaderField: "User-Agent")

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            return []
        }
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let feed = json["feed"] as? [String: Any],
              let entries = feed["entry"] as? [[String: Any]] else {
            return []
        }

        var list = [IrasutoyaImage]()
        for entry in entries {
            let title = (entry["title"] as? [String: Any])?["$t"] as? String ?? ""
            let links = entry["link"] as? [[String: Any]] ?? []
            let altLink = links.first(where: { ($0["rel"] as? String) == "alternate" })?["href"] as? String ?? ""

            var thumb = (entry["media$thumbnail"] as? [String: Any])?["url"] as? String ?? ""
            if thumb.isEmpty, let contentHtml = (entry["content"] as? [String: Any])?["$t"] as? String {
                let pattern = #"src="(https?://[^"]+\.(?:png|jpg|jpeg|gif))""#
                if let regex = try? NSRegularExpression(pattern: pattern, options: .caseInsensitive),
                   let match = regex.firstMatch(in: contentHtml, range: NSRange(contentHtml.startIndex..., in: contentHtml)),
                   let range = Range(match.range(at: 1), in: contentHtml) {
                    thumb = String(contentHtml[range])
                }
            }

            guard !altLink.isEmpty, !thumb.isEmpty else { continue }
            // Upscale small Blogger thumbnails for the editor picker.
            let upscaled = thumb
                .replacingOccurrences(of: "/s72-c/", with: "/s400-c/")
                .replacingOccurrences(of: "/s72-c$", with: "/s400-c", options: .regularExpression)
                .replacingOccurrences(of: "/s1600/", with: "/s400/")
            list.append(IrasutoyaImage(url: altLink, thumb: upscaled, title: title))
        }
        return list
    }
}
