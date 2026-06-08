import SwiftUI

struct AvatarEditorView: View {
    let keyword: String
    
    @Environment(\.dismiss) private var dismiss
    @StateObject private var db = LocalDB.shared
    @StateObject private var theme = ThemeManager.shared
    @StateObject private var i18n = I18nManager.shared
    
    @State private var layers: [AvatarLayer] = []
    @State private var selectedId: String? = nil
    @State private var cropMode = false
    
    @State private var searchQuery = ""
    @State private var stickers: [IrasutoyaImage] = []
    @State private var activeCategory: String? = nil // Nil = popular
    @State private var searchingStickers = false
    @State private var saving = false
    
    // Drag gestures temporary starting state
    @State private var startX: Double = 0.0
    @State private var startY: Double = 0.0
    @State private var startCropX: Double = 0.0
    @State private var startCropY: Double = 0.0
    
    private let categories = [
        (label: "popularStickers", query: nil as String?),
        (label: "人物", query: "人物"),
        (label: "表情", query: "表情"),
        (label: "動物", query: "動物"),
        (label: "女性", query: "女性"),
        (label: "男性", query: "男性"),
        (label: "子供", query: "子供"),
        (label: "食べ物", query: "食べ物")
    ]
    
    private let baseSize: Double = 90.0

    private var activeLayer: AvatarLayer? {
        layers.first(where: { $0.id == selectedId })
    }

    var body: some View {
        VStack(spacing: 0) {
            // Header Bar
            HStack {
                Button(action: { dismiss() }) {
                    Image(systemName: "chevron.left")
                        .font(.title2)
                        .foregroundColor(theme.colors.primary)
                }
                .accessibilityIdentifier("avatar.backButton")
                Text("✨ \(keyword)")
                    .font(.headline)
                    .foregroundColor(theme.colors.text)
                    .lineLimit(1)
                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.top, 10)
            .padding(.bottom, 10)
            .background(theme.colors.card)
            
            // Canvas Wrapper
            GeometryReader { geometry in
                let W = geometry.size.width
                let H = 300.0
                let scaleFactor = W / 300.0
                
                ZStack {
                    // Canvas background
                    Rectangle()
                        .fill(theme.mode == .dark ? Color(white: 0.1) : Color(white: 0.94))
                        .frame(width: W, height: H)
                    
                    if layers.isEmpty {
                        VStack(spacing: 6) {
                            Text("(˶ᵔ ᵕ ᵔ˶)")
                                .font(.title3)
                                .foregroundColor(theme.colors.textMuted)
                            Text(i18n.t("tapToAddToCanvas"))
                                .font(.caption2)
                                .foregroundColor(theme.colors.textMuted)
                        }
                    } else {
                        // Staked layers sorted by zIndex
                        let sorted = layers.sorted(by: { $0.zIndex < $1.zIndex })
                        ForEach(sorted) { layer in
                            let isSelected = selectedId == layer.id
                            let size = baseSize * layer.scale * scaleFactor
                            let cropX = (layer.cropX ?? 0.0) * scaleFactor
                            let cropY = (layer.cropY ?? 0.0) * scaleFactor
                            let cropScale = layer.cropScale ?? 1.0
                            let rotation = layer.rotation ?? 0.0
                            
                            ZStack {
                                if let url = URL(string: layer.imageUrl) {
                                    AsyncImage(url: url) { image in
                                        image
                                            .resizable()
                                            .aspectRatio(contentMode: .fit)
                                            .scaleEffect(cropScale)
                                            .offset(x: cropX, y: cropY)
                                            .frame(width: size, height: size)
                                            .clipped()
                                            .rotationEffect(Angle(degrees: rotation))
                                    } placeholder: {
                                        ProgressView()
                                    }
                                }
                            }
                            .frame(width: size, height: size)
                            .border(isSelected ? (cropMode ? Color.green : Color.blue) : Color.clear, width: 2)
                            .cornerRadius(4)
                            .position(x: (layer.x + (baseSize * layer.scale) / 2.0) * scaleFactor,
                                      y: (layer.y + (baseSize * layer.scale) / 2.0) * scaleFactor)
                            .gesture(
                                DragGesture(minimumDistance: 0)
                                    .onChanged { value in
                                        if selectedId != layer.id {
                                            selectedId = layer.id
                                            cropMode = false
                                        }
                                        
                                        // On drag start values record (since gesture state updates continuously, check if starting)
                                        if value.translation == .zero {
                                            startX = layer.x
                                            startY = layer.y
                                            startCropX = layer.cropX ?? 0.0
                                            startCropY = layer.cropY ?? 0.0
                                        }
                                        
                                        let dx = value.translation.width / scaleFactor
                                        let dy = value.translation.height / scaleFactor
                                        
                                        if let idx = layers.firstIndex(where: { $0.id == layer.id }) {
                                            if cropMode {
                                                var modified = layers[idx]
                                                modified.cropX = startCropX + dx
                                                modified.cropY = startCropY + dy
                                                let clamped = clampCrop(layer: modified)
                                                layers[idx].cropX = clamped.cropX
                                                layers[idx].cropY = clamped.cropY
                                            } else {
                                                // Clamp movements inside canvas
                                                let maxX = 300.0 - (baseSize * layer.scale)
                                                let maxY = 300.0 - (baseSize * layer.scale)
                                                layers[idx].x = max(0.0, min(maxX, startX + dx))
                                                layers[idx].y = max(0.0, min(maxY, startY + dy))
                                            }
                                        }
                                    }
                            )
                        }
                    }
                }
                .frame(width: W, height: H)
                .accessibilityIdentifier("avatar.canvas")
            }
            .frame(height: 300)
            
            // Layer Controls Panel
            VStack(spacing: 8) {
                // Toolbar rows
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        // Crop/Move Mode toggle
                        Button(action: {
                            if activeLayer != nil { cropMode.toggle() }
                        }) {
                            Text(cropMode ? i18n.t("moveModeBtn") : i18n.t("cropModeBtn"))
                                .font(.system(size: 11, weight: .bold))
                                .padding(.horizontal, 12)
                                .padding(.vertical, 6)
                                .background(cropMode ? theme.colors.primaryBg : theme.colors.divider)
                                .foregroundColor(cropMode ? theme.colors.primary : theme.colors.text)
                                .cornerRadius(99)
                        }
                        .disabled(activeLayer == nil)
                        .opacity(activeLayer == nil ? 0.4 : 1.0)
                        .accessibilityIdentifier("avatar.cropButton")
                        
                        if cropMode {
                            toolbarBtn(i18n.t("zoomIn"),      needsSelection: false) { cropZoom(0.15) }
                            toolbarBtn(i18n.t("zoomOut"),     needsSelection: false) { cropZoom(-0.15) }
                            toolbarBtn(i18n.t("fitToCanvas"), needsSelection: false) { resetCrop() }
                        }

                        Divider().frame(height: 16)

                        toolbarBtn("＋", a11y: "avatar.scaleUpButton",   size: 12) { scaleLayer(0.15) }
                        toolbarBtn("－", a11y: "avatar.scaleDownButton",  size: 12) { scaleLayer(-0.15) }
                        toolbarBtn("⟲", size: 12) { rotateLayer(-15) }
                        toolbarBtn("⟳", size: 12) { rotateLayer(15) }
                        toolbarBtn(i18n.t("layerForward")) { bringForward() }
                        toolbarBtn(i18n.t("layerBack")) { sendBack() }
                        toolbarBtn(i18n.t("delete"), a11y: "avatar.deleteLayerButton", destructive: true) { deleteSelected() }
                    }
                    .padding(.horizontal, 12)
                }
                .frame(height: 38)
                
                // Save actions row
                HStack(spacing: 8) {
                    Button(action: {
                        Task { await saveComposition() }
                    }) {
                        HStack {
                            Image(systemName: "checkmark")
                            Text(saving ? "..." : i18n.t("saveAvatar"))
                        }
                        .bold()
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 9)
                        .background(theme.colors.primary)
                        .cornerRadius(10)
                    }
                    .disabled(saving)
                    .accessibilityIdentifier("avatar.saveButton")
                    
                    Button(action: {
                        Task { await applyAsWallpaper() }
                    }) {
                        Text(i18n.t("setAsWallpaper"))
                            .bold()
                            .foregroundColor(theme.colors.primary)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 9)
                            .background(theme.colors.primaryBg)
                            .cornerRadius(10)
                    }
                    .disabled(layers.isEmpty)
                    .opacity(layers.isEmpty ? 0.5 : 1.0)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
            }
            .background(theme.colors.card)
            .overlay(
                Rectangle()
                    .frame(height: 0.5)
                    .foregroundColor(theme.colors.divider),
                alignment: .bottom
            )
            
            // Search row for Stickers
            HStack(spacing: 8) {
                TextField(i18n.t("searchStickers"), text: $searchQuery, onCommit: {
                    Task { await performSearch() }
                })
                .padding(8)
                .background(theme.colors.card)
                .cornerRadius(10)
                .foregroundColor(theme.colors.text)
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(theme.colors.border, lineWidth: 1))
                .accessibilityIdentifier("avatar.stickerSearchField")
                
                Button(action: {
                    Task { await performSearch() }
                }) {
                    Text("🔍")
                        .font(.title3)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(theme.colors.primary)
                        .cornerRadius(10)
                }
                .accessibilityIdentifier("avatar.stickerSearchButton")
            }
            .padding(.horizontal, 12)
            .padding(.top, 10)
            .padding(.bottom, 6)
            
            // Sticker categories chips
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(categories, id: \.label) { cat in
                        let isSelected = activeCategory == cat.query
                        Button(action: {
                            Task { await selectCategory(cat.query) }
                        }) {
                            Text(cat.query == nil ? i18n.t("popularStickers") : cat.label)
                                .font(.system(size: 12, weight: isSelected ? .bold : .medium))
                                .padding(.horizontal, 11)
                                .padding(.vertical, 5)
                                .background(isSelected ? theme.colors.primary : theme.colors.divider)
                                .foregroundColor(isSelected ? .white : theme.colors.textSub)
                                .cornerRadius(999)
                        }
                    }
                }
                .padding(.horizontal, 12)
                .padding(.bottom, 8)
            }
            
            // Grid of search results
            if searchingStickers {
                Spacer()
                ProgressView().tint(theme.colors.primary)
                Spacer()
            } else if stickers.isEmpty {
                VStack(spacing: 8) {
                    Spacer()
                    Text("(´• ω •`)")
                        .font(.largeTitle)
                    Text(i18n.t("noStickersFound"))
                        .font(.subheadline)
                        .foregroundColor(theme.colors.textMuted)
                    Spacer()
                }
            } else {
                ScrollView {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 3), spacing: 8) {
                        ForEach(stickers) { sticker in
                            Button(action: { addLayer(url: sticker.thumb) }) {
                                ZStack {
                                    if let url = URL(string: sticker.thumb) {
                                        AsyncImage(url: url) { image in
                                            image
                                                .resizable()
                                                .aspectRatio(contentMode: .fit)
                                                .frame(width: 80, height: 80)
                                        } placeholder: {
                                            Color.gray.opacity(0.1)
                                                .frame(width: 80, height: 80)
                                        }
                                        
                                        // Plus overlay
                                        VStack {
                                            Spacer()
                                            HStack {
                                                Spacer()
                                                Text("＋")
                                                    .font(.caption2)
                                                    .bold()
                                                    .foregroundColor(.white)
                                                    .frame(width: 16, height: 16)
                                                    .background(theme.colors.primary)
                                                    .clipShape(Circle())
                                                    .padding(4)
                                            }
                                        }
                                    }
                                }
                                .frame(maxWidth: .infinity)
                                .aspectRatio(1.0, contentMode: .fit)
                                .background(theme.colors.card)
                                .cornerRadius(8)
                                .overlay(RoundedRectangle(cornerRadius: 8).stroke(theme.colors.border, lineWidth: 1))
                            }
                            .buttonStyle(PlainButtonStyle())
                        }
                    }
                    .padding(.horizontal, 12)
                    .padding(.bottom, 20)
                }
            }
        }
        .accessibilityIdentifier("avatar.screen")
        .background(theme.colors.bg)
        .navigationBarBackButtonHidden()
        .onAppear {
            loadComposition()
            Task { await selectCategory(nil) }
        }
    }
    
    // MARK: - Layer Logic
    
    private func loadComposition() {
        let comp = db.compositions[keyword] ?? []
        self.layers = comp
        if let first = comp.first {
            selectedId = first.id
        }
    }
    
    private func addLayer(url: String) {
        let maxZ = layers.reduce(0) { max($0, $1.zIndex) }
        // Start in the center of 300x300 canvas
        let newLayer = AvatarLayer(
            imageUrl: url,
            x: 150.0 - (baseSize / 2.0),
            y: 150.0 - (baseSize / 2.0),
            scale: 1.0,
            cropX: 0.0,
            cropY: 0.0,
            cropScale: 1.0,
            rotation: 0.0,
            zIndex: maxZ + 1
        )
        layers.append(newLayer)
        selectedId = newLayer.id
        cropMode = false
    }
    
    private func deleteSelected() {
        guard let id = selectedId else { return }
        layers.removeAll(where: { $0.id == id })
        selectedId = layers.last?.id
        cropMode = false
    }
    
    private func scaleLayer(_ delta: Double) {
        guard let id = selectedId, let idx = layers.firstIndex(where: { $0.id == id }) else { return }
        let currentScale = layers[idx].scale
        let nextScale = max(0.3, min(3.0, currentScale + delta))
        layers[idx].scale = nextScale
        
        // Clamp bounds after resize
        let maxX = 300.0 - (baseSize * nextScale)
        let maxY = 300.0 - (baseSize * nextScale)
        layers[idx].x = max(0.0, min(maxX, layers[idx].x))
        layers[idx].y = max(0.0, min(maxY, layers[idx].y))
        
        let clamped = clampCrop(layer: layers[idx])
        layers[idx].cropX = clamped.cropX
        layers[idx].cropY = clamped.cropY
    }
    
    private func rotateLayer(_ delta: Double) {
        guard let id = selectedId, let idx = layers.firstIndex(where: { $0.id == id }) else { return }
        let currentRot = layers[idx].rotation ?? 0.0
        layers[idx].rotation = Double((Int(currentRot + delta) + 360) % 360)
    }
    
    private func bringForward() {
        guard let id = selectedId, let idx = layers.firstIndex(where: { $0.id == id }) else { return }
        let maxZ = layers.reduce(0) { max($0, $1.zIndex) }
        layers[idx].zIndex = maxZ + 1
    }
    
    private func sendBack() {
        guard let id = selectedId, let idx = layers.firstIndex(where: { $0.id == id }) else { return }
        let minZ = layers.reduce(0) { min($0, $1.zIndex) }
        layers[idx].zIndex = minZ - 1
    }
    
    private func cropZoom(_ delta: Double) {
        guard let id = selectedId, let idx = layers.firstIndex(where: { $0.id == id }) else { return }
        let currentZoom = layers[idx].cropScale ?? 1.0
        let nextZoom = max(1.0, min(3.0, currentZoom + delta))
        layers[idx].cropScale = nextZoom
        let clamped = clampCrop(layer: layers[idx])
        layers[idx].cropX = clamped.cropX
        layers[idx].cropY = clamped.cropY
    }
    
    private func resetCrop() {
        guard let id = selectedId, let idx = layers.firstIndex(where: { $0.id == id }) else { return }
        layers[idx].cropX = 0.0
        layers[idx].cropY = 0.0
        layers[idx].cropScale = 1.0
    }
    
    private func clampCrop(layer: AvatarLayer) -> (cropX: Double, cropY: Double) {
        let size = baseSize * layer.scale
        let cropScale = layer.cropScale ?? 1.0
        let maxOffset = max(0.0, (size * cropScale - size) / 2.0)
        let cx = max(-maxOffset, min(maxOffset, layer.cropX ?? 0.0))
        let cy = max(-maxOffset, min(maxOffset, layer.cropY ?? 0.0))
        return (cx, cy)
    }
    
    // MARK: - Save Actions
    
    private func saveComposition() async {
        saving = true
        db.setOshiComposition(keyword: keyword, layers: layers)
        
        // Also update avatar preview image representation to be the top layer sticker
        if let topLayer = layers.sorted(by: { $0.zIndex < $1.zIndex }).last {
            db.setOshiAvatar(keyword: keyword, imageUrl: topLayer.imageUrl)
        }
        
        saving = false
        dismiss()
    }
    
    private func applyAsWallpaper() async {
        if let topLayer = layers.sorted(by: { $0.zIndex < $1.zIndex }).last {
            db.setWallpaper(url: topLayer.imageUrl)
        }
    }
    
    // MARK: - Sticker Fetching
    
    private func selectCategory(_ query: String?) async {
        activeCategory = query
        searchQuery = ""
        searchingStickers = true
        
        do {
            if let query = query {
                stickers = try await NetworkManager.shared.searchIrasutoya(query: query)
            } else {
                stickers = try await NetworkManager.shared.getPopularIrasutoya()
            }
        } catch {
            AppLogger.network.error("Stickers load error: \(error.localizedDescription)")
            stickers = []
        }

        searchingStickers = false
    }

    private func performSearch() async {
        guard !searchQuery.isEmpty else { return }
        activeCategory = "__search__"
        searchingStickers = true

        do {
            stickers = try await NetworkManager.shared.searchIrasutoya(query: searchQuery)
        } catch {
            AppLogger.network.error("Stickers search error: \(error.localizedDescription)")
            stickers = []
        }

        searchingStickers = false
    }

    // MARK: - Toolbar Button Helper

    @ViewBuilder
    private func toolbarBtn(
        _ label: String,
        a11y: String? = nil,
        size: CGFloat = 11,
        destructive: Bool = false,
        needsSelection: Bool = true,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(label)
                .font(.system(size: size, weight: .bold))
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(destructive ? Color(red: 1.0, green: 0.9, blue: 0.9) : theme.colors.divider)
                .foregroundColor(destructive ? .red : theme.colors.text)
                .cornerRadius(99)
        }
        .disabled(needsSelection && activeLayer == nil)
        .opacity(needsSelection && activeLayer == nil ? 0.4 : 1.0)
        .accessibilityIdentifier(a11y ?? "")
    }
}
