/* PDF Küçült — Zotero eklentisi.
 *
 * Zotero'daki PDF eklerini PDF Küçült motoruyla (motor.py) küçültür ve dosyayı yerinde
 * değiştirir: ek öğesi, notlar, açıklamalar (annotation) ve etiketler aynen kalır. Büyük
 * orijinal macOS Çöp Kutusu'na taşınır (geri alınabilir). Zotero'nun okuyucuda sayfa
 * döndürürken yaptığı gibi dosya yeni tarihle yazılır; senkronizasyon küçük dosyayı yükler.
 */

PDFKucult = {
	id: null,
	rootURI: null,
	menuIDs: [],
	observerID: null,
	chain: Promise.resolve(),
	running: 0,
	cancelled: false,
	currentProc: null,
	status: new Map(),   // libraryKey -> seviye ("okuma"; eski kayıtlarda "onerilen" | "asiri" | "az") | "optimum"
	missing: new Set(),  // bu bilgisayarda dosyası olmayan ekler (son taramaya göre)
	columnKey: null,
	gainColumnKey: null,
	pageColumnKey: null,
	records: {},         // kayıt dosyasının bellekteki kopyası (kazanç sütunu için)
	fileInfo: new Map(), // ek id -> { size, pages, t } (KB/sayfa sütunu için önbellek)
	infoQueue: new Map(),
	infoBusy: false,
	PAGE_WARN_KB: 150,   // bunun üstü incelemeye değer (siyah-beyaz taramada normal 15–100 KB)

	LEVEL: "okuma",      // tek mod: motorun en sıkı seviyesi
	RANK: { az: 0, onerilen: 1, asiri: 2, okuma: 3 },  // eski kayıtlar: daha düşük seviyeler yeniden işlenir
	PREF_AUTO: "extensions.pdfkucult.autoLevel",
	PREF_OCR: "extensions.pdfkucult.ocr",

	// ------------------------------------------------------------ kurulum

	init({ id, rootURI }) {
		this.id = id;
		this.rootURI = rootURI;
		let home = Services.dirsvc.get("Home", Ci.nsIFile).path;
		this.home = home;
		this.logFile = Zotero.isWin
			? PathUtils.join(Services.env.get("LOCALAPPDATA") || home, "PDFKucult", "Raporlar", "zotero.log")
			: PathUtils.join(home, "Library", "Logs", "PDFKucult", "zotero.log");
		this.registryFile = PathUtils.join(Zotero.DataDirectory.dir, "pdfkucult.json");
		this.registerMenus();
		this.registerColumn();
		this.loadRegistry().then(reg => this.buildStatus(reg));
		this.observerID = Zotero.Notifier.registerObserver(
			{ notify: (...args) => this.onNotify(...args) }, ["item"], "pdfkucult");
	},

	// ------------------------------------------------------------ "Küçültme" sütunu

	registerColumn() {
		this.columnKey = Zotero.ItemTreeManager.registerColumn({
			dataKey: "pdfkucult-durum",
			label: "Küçültme",
			pluginID: this.id,
			enabledTreeIDs: ["main"],
			flex: 0,
			width: "130",
			zoteroPersist: ["width", "hidden", "sortDirection"],
			dataProvider: (item) => this.columnText(item),
		});
		this.gainColumnKey = Zotero.ItemTreeManager.registerColumn({
			dataKey: "pdfkucult-kazanc",
			label: "Kazanç",
			pluginID: this.id,
			enabledTreeIDs: ["main"],
			flex: 0,
			width: "120",
			zoteroPersist: ["width", "hidden", "sortDirection"],
			// sıralama için "anahtar|görünen metin" döner; hücrede yalnızca metin gösterilir
			dataProvider: (item) => this.gainData(item),
			renderCell: (index, data, column, isFirstColumn, doc) => {
				let cell = doc.createElement("span");
				cell.className = `cell ${column.className}`;
				cell.textContent = (data || "").split("|").slice(1).join("|");
				return cell;
			},
		});
		this.pageColumnKey = Zotero.ItemTreeManager.registerColumn({
			dataKey: "pdfkucult-sayfa",
			label: "KB/sayfa",
			pluginID: this.id,
			enabledTreeIDs: ["main"],
			flex: 0,
			width: "90",
			zoteroPersist: ["width", "hidden", "sortDirection"],
			dataProvider: (item) => this.pageData(item),
			renderCell: (index, data, column, isFirstColumn, doc) => {
				let cell = doc.createElement("span");
				cell.className = `cell ${column.className}`;
				cell.textContent = (data || "").split("|").slice(1).join("|");
				return cell;
			},
		});
	},

	/** "sıralama anahtarı|metin": ekin (ya da makalenin bütün PDF'lerinin) sayfa başına boyutu. */
	pageData(item) {
		let atts = this.pdfFilesOf(item);
		let size = 0, pages = 0;
		for (let a of atts) {
			let info = this.fileInfo.get(a.id);
			// sayfa sayısı henüz bilinmiyorsa (tam metin dizini yok) arada bir yeniden dene
			if (!info || (!info.pages && Date.now() - info.t > 60000)) {
				this.queueInfo(a);
			}
			if (info && info.pages) {
				size += info.size;
				pages += info.pages;
			}
		}
		if (!pages) {
			return "";
		}
		let kb = Math.round(size / 1024 / pages);
		return `${String(kb).padStart(8, "0")}|${kb >= this.PAGE_WARN_KB ? "⚠ " : ""}${kb} KB`;
	},

	queueInfo(att) {
		this.infoQueue.set(att.id, att);
		if (!this.infoBusy) {
			this.infoBusy = true;
			this.loadInfo().catch(e => Zotero.logError(e)).finally(() => { this.infoBusy = false; });
		}
	},

	async loadInfo() {
		// liste çizilirken gelen istekleri biriktirip toplu işle; dosya boyutu diskten,
		// sayfa sayısı Zotero'nun tam metin dizininden (PDF'i açmadan)
		while (this.infoQueue.size) {
			await Zotero.Promise.delay(200);
			let atts = [...this.infoQueue.values()].slice(0, 400);
			atts.forEach(a => this.infoQueue.delete(a.id));
			let ids = atts.map(a => a.id);
			let rows = await Zotero.DB.queryAsync(
				`SELECT itemID, totalPages FROM fulltextItems WHERE itemID IN (${ids.map(() => "?").join(",")})`, ids);
			let pages = new Map((rows || []).map(r => [r.itemID, r.totalPages || 0]));
			for (let a of atts) {
				let info = { size: 0, pages: 0, t: Date.now() };
				let path = await a.getFilePathAsync();
				if (path && await IOUtils.exists(path)) {
					info.size = (await IOUtils.stat(path)).size;
					info.pages = pages.get(a.id) || 0;
				}
				this.fileInfo.set(a.id, info);
			}
			try {
				Zotero.ItemTreeManager.refreshColumns();
			}
			catch (e) {}
		}
	},

	gainData(item) {
		let before = 0, after = 0;
		for (let a of this.pdfFilesOf(item)) {
			let r = this.records[a.libraryKey];
			if (r && r.original && r.size) {
				before += r.original;
				after += r.size;
			}
		}
		if (!before) {
			return "";
		}
		let pct = Math.round(100 * (1 - after / before));
		let key = String(1000 + pct).padStart(4, "0");
		let text = after <= before ? `%${pct} · −${this.human(before - after)}` : `+${this.human(after - before)} (OCR)`;
		return `${key}|${text}`;
	},

	buildStatus(registry) {
		let m = new Map();
		for (let [key, rec] of Object.entries(registry || {})) {
			let compressed = "original" in rec || rec.isaret;
			m.set(key, compressed ? rec.level : "optimum");
		}
		this.status = m;
		this.records = registry || {};
		try {
			Zotero.ItemTreeManager.refreshColumns();
		}
		catch (e) {}
	},

	pdfFilesOf(item) {
		if (item.isRegularItem()) {
			return item.getAttachments().map(id => Zotero.Items.get(id))
				.filter(a => a && !a.deleted && a.isPDFAttachment() && a.isFileAttachment());
		}
		return item.isPDFAttachment() && item.isFileAttachment() ? [item] : [];
	},

	columnText(item) {
		let atts = this.pdfFilesOf(item);
		if (!atts.length) {
			return "";
		}
		let st = atts.map(a => this.missing.has(a.libraryKey) ? "yok" : (this.status.get(a.libraryKey) || "hayir"));
		let notDone = st.filter(s => s == "hayir").length;
		if (notDone) {
			return atts.length > 1 ? `○ Küçültülmedi (${notDone}/${atts.length})` : "○ Küçültülmedi";
		}
		if (st.every(s => s == "yok")) {
			return "· Dosya yok";
		}
		return st.some(s => this.RANK[s] !== undefined) ? "✓ Küçültüldü" : "✓ Zaten küçük";
	},

	shutdown() {
		this.cancelled = true;
		this.currentProc?.kill();
		for (let id of this.menuIDs) {
			Zotero.MenuManager.unregisterMenu(id);
		}
		this.menuIDs = [];
		for (let key of [this.columnKey, this.gainColumnKey, this.pageColumnKey]) {
			if (key) {
				Zotero.ItemTreeManager.unregisterColumn(key);
			}
		}
		if (this.observerID) {
			Zotero.Notifier.unregisterObserver(this.observerID);
		}
	},

	registerMenus() {
		let icon = this.rootURI + "icon.svg";
		let itemMenu = Zotero.MenuManager.registerMenu({
			menuID: "pdfkucult-item",
			pluginID: this.id,
			target: "main/library/item",
			menus: [{
				menuType: "menuitem",
				l10nID: "pdfkucult-compress",
				icon,
				onShowing: (event, context) => context.setVisible(this.mayHavePDFs(context.items)),
				onCommand: (event, context) => this.compressSelection(context.items),
			}],
		});
		let toolsMenu = Zotero.MenuManager.registerMenu({
			menuID: "pdfkucult-tools",
			pluginID: this.id,
			target: "main/menubar/tools",
			menus: [{
				menuType: "submenu",
				l10nID: "pdfkucult-menu",
				icon,
				menus: [
					{
						menuType: "menuitem",
						l10nID: "pdfkucult-library",
						onCommand: () => this.compressLibrary(),
					},
					{
						menuType: "menuitem",
						l10nID: "pdfkucult-scan",
						onCommand: () => this.scanLibrary(),
					},
					{
						menuType: "menuitem",
						l10nID: "pdfkucult-summary",
						onCommand: () => this.showSummary(),
					},
					{
						menuType: "menuitem",
						l10nID: "pdfkucult-stop",
						onShowing: (event, context) => context.setEnabled(this.running > 0),
						onCommand: () => this.stop(),
					},
					{ menuType: "separator" },
					{
						menuType: "menuitem",
						l10nID: "pdfkucult-auto",
						onShowing: (event, context) => context.setL10nArgs(
							JSON.stringify({ sel: this.autoLevel() != "off" ? "✓ " : "" })),
						onCommand: () => Zotero.Prefs.set(this.PREF_AUTO, this.autoLevel() == "off" ? this.LEVEL : "off", true),
					},
					{
						menuType: "menuitem",
						l10nID: "pdfkucult-ocr",
						onShowing: (event, context) => context.setL10nArgs(
							JSON.stringify({ sel: this.ocrOn() ? "✓ " : "" })),
						onCommand: () => Zotero.Prefs.set(this.PREF_OCR, !this.ocrOn(), true),
					},
					{
						menuType: "menuitem",
						l10nID: "pdfkucult-log",
						onCommand: () => this.openLog(),
					},
				],
			}],
		});
		this.menuIDs = [itemMenu, toolsMenu].filter(Boolean);
	},

	ocrOn() {
		return Zotero.Prefs.get(this.PREF_OCR, true) !== false;
	},

	autoLevel() {
		// eski sürümlerin seviye değerleri (onerilen/asiri/az) "açık" sayılır
		return Zotero.Prefs.get(this.PREF_AUTO, true) == "off" ? "off" : this.LEVEL;
	},

	// ------------------------------------------------------------ öğe seçimi

	mayHavePDFs(items) {
		return (items || []).some(item => item.isPDFAttachment()
			|| (item.isRegularItem() && item.numAttachments() > 0));
	},

	pdfAttachmentsOf(items) {
		let out = new Map();
		for (let item of items || []) {
			let atts = item.isRegularItem()
				? item.getAttachments().map(id => Zotero.Items.get(id))
				: [item];
			for (let att of atts) {
				if (att && !att.deleted && att.isPDFAttachment() && att.isFileAttachment()) {
					out.set(att.id, att);
				}
			}
		}
		return [...out.values()];
	},

	// ------------------------------------------------------------ komutlar

	compressSelection(items) {
		let atts = this.pdfAttachmentsOf(items);
		if (!atts.length) {
			this.notify("Seçili öğelerde PDF eki bulunamadı.");
			return;
		}
		this.runBatch(atts, this.LEVEL, `Akıllı sıkıştırma — ${atts.length} PDF`);
	},

	async compressLibrary() {
		let win = Zotero.getMainWindow();
		let libraryID = win?.ZoteroPane?.getSelectedLibraryID() || Zotero.Libraries.userLibraryID;
		let library = Zotero.Libraries.get(libraryID);
		if (!library.filesEditable) {
			this.notify("Bu kütüphanede dosyaları değiştirme yetkiniz yok.");
			return;
		}
		let items = await Zotero.Items.getAll(libraryID, false, false);
		let atts = [];
		let total = 0;
		for (let att of items) {
			if (att.isPDFAttachment() && att.isFileAttachment() && !att.deleted) {
				let path = await att.getFilePathAsync();
				if (path) {
					let size = (await IOUtils.stat(path)).size;
					atts.push({ att, size });
					total += size;
				}
			}
		}
		if (!atts.length) {
			this.notify("Bu kütüphanede diskte duran PDF bulunamadı.");
			return;
		}
		let ok = Services.prompt.confirm(win, "PDF Küçült",
			`“${library.name}” kütüphanesindeki ${atts.length} PDF (${this.human(total)}) akıllı sıkıştırılacak: ` +
			"metin okunur kalır; görseller gri, taranmış sayfalar siyah-beyaz olur.\n\n" +
			"Daha önce küçültülmüş dosyalar atlanır. Küçülen her dosyanın büyük orijinali " +
			"Çöp Kutusu'na taşınır (oradan geri alınabilir). Notlar, açıklamalar ve etiketler " +
			"aynen kalır.\n\nİşlem uzun sürebilir; Araçlar › PDF Küçült › Durdur ile " +
			"istediğiniz an durdurabilirsiniz. Devam edilsin mi?");
		if (!ok) {
			return;
		}
		atts.sort((a, b) => b.size - a.size); // en büyükler önce: kazanç erken gelir
		this.runBatch(atts.map(x => x.att), this.LEVEL, "Akıllı sıkıştırma — kütüphane");
	},

	stop() {
		this.cancelled = true;
		this.currentProc?.kill();
	},

	// ------------------------------------------------------------ toplu iş

	runBatch(atts, level, headline) {
		this.cancelled = false;
		this.running++;
		let pw = new Zotero.ProgressWindow({ closeOnClick: false });
		pw.changeHeadline(headline);
		pw.show();
		let detailed = atts.length <= 8;
		let summaryLine = detailed ? null : new pw.ItemProgress("attachmentPDF", "Başlıyor…");
		let stats = { done: 0, saved: 0, before: 0, after: 0, failed: 0 };

		this.enqueue(async () => {
			try {
				for (let att of atts) {
					if (this.cancelled) {
						break;
					}
					let title = this.titleOf(att);
					let line = detailed ? new pw.ItemProgress("attachmentPDF", `${title} — küçültülüyor…`) : null;
					summaryLine?.setText(`${stats.done + 1}/${atts.length} — ${title}`);
					let r = await this.compressOne(att, level);
					stats.done++;
					if (r.status == "ok") {
						stats.before += r.before;
						stats.after += r.after;
						stats.saved += r.before - r.after;
						line?.setText(`${title} — ${this.human(r.before)} → ${this.human(r.after)}` + (r.ocr ? ` (+OCR)` : ""));
						line?.setProgress(100);
					}
					else if (r.status == "error") {
						stats.failed++;
						line?.setText(`${title} — hata: ${r.message}`);
						line?.setError();
					}
					else {
						line?.setText(`${title} — ${r.message}`);
						line?.setProgress(100);
					}
					summaryLine?.setProgress(Math.round(100 * stats.done / atts.length));
				}
			}
			finally {
				this.running--;
				let msg = `${stats.done}/${atts.length} PDF işlendi` +
					(stats.saved > 0 ? ` — ${this.human(stats.saved)} kazanıldı` : "") +
					(stats.failed ? `, ${stats.failed} hata` : "") +
					(this.cancelled ? " (durduruldu)" : "");
				if (summaryLine) {
					summaryLine.setText(msg);
					summaryLine.setProgress(100);
				}
				else {
					pw.addDescription(msg);
				}
				pw.startCloseTimer(detailed ? 8000 : 15000);
				this.cancelled = false;
			}
		});
	},

	enqueue(fn) {
		// motor aynı anda tek dosya işler (kendi içinde zaten çok çekirdekli)
		this.chain = this.chain.then(fn).catch(e => Zotero.logError(e));
		return this.chain;
	},

	// ------------------------------------------------------------ otomatik mod

	onNotify(event, type, ids, extraData) {
		if (type == "item" && event == "modify") {
			ids.forEach(id => this.fileInfo.delete(id));  // dosya değişmiş olabilir: KB/sayfa yeniden ölçülsün
		}
		if (event != "add" || type != "item") {
			return;
		}
		let level = this.autoLevel();
		if (level == "off") {
			return;
		}
		for (let id of ids) {
			let item = Zotero.Items.get(id);
			if (item && item.isPDFAttachment() && item.isFileAttachment()) {
				this.autoCompress(item, level);
			}
		}
	},

	async autoCompress(att, level) {
		// "Find Available PDF" önce eki, sonra dosyayı yazabilir: dosya gelip boyutu sabitlenene
		// kadar bekle (en çok 2 dk). Senkronizasyondan gelen ve dosyası inmeyen ekler atlanır.
		let lastSize = -1;
		for (let i = 0; i < 60; i++) {
			await Zotero.Promise.delay(2000);
			if (this.cancelled || !Zotero.Items.get(att.id)) {
				return;
			}
			let path = await att.getFilePathAsync();
			if (path && await IOUtils.exists(path)) {
				let size = (await IOUtils.stat(path)).size;
				if (size > 0 && size == lastSize) {
					break;
				}
				lastSize = size;
			}
			if (i == 59) {
				return;
			}
		}
		this.enqueue(async () => {
			this.running++;
			try {
				let r = await this.compressOne(att, level);
				if (r.status == "ok") {
					this.notify(`${this.titleOf(att)}\n${this.human(r.before)} → ${this.human(r.after)}`
						+ (r.ocr ? `\n${r.ocr} sayfaya metin (OCR) eklendi` : ""), "PDF otomatik küçültüldü");
				}
			}
			finally {
				this.running--;
			}
		});
	},

	// ------------------------------------------------------------ tek dosya

	async compressOne(att, level) {
		let title = this.titleOf(att);
		try {
			if (!Zotero.Libraries.get(att.libraryID).filesEditable) {
				return { status: "skip", message: "kütüphane salt okunur" };
			}
			let path = await att.getFilePathAsync();
			if (!path || !(await IOUtils.exists(path))) {
				return { status: "skip", message: "dosya diskte yok" };
			}
			if (!(await this.engine())) {
				throw new Error("PDF Küçült uygulaması bulunamadı. Önce PDF Küçült uygulamasını kurun "
					+ (Zotero.isWin ? "(PDF-Kucult-Kurulum.exe)." : "(Uygulamalar klasörüne)."));
			}
			let before = await IOUtils.stat(path);
			let registry = await this.loadRegistry();
			let rec = registry[att.libraryKey];
			if (rec && rec.size == before.size && this.RANK[rec.level] >= this.RANK[level]) {
				return { status: "skip", message: "daha önce küçültülmüş" };
			}

			let tmpDir = PathUtils.join(PathUtils.tempDir, "pdfkucult");
			await IOUtils.makeDirectory(tmpDir, { ignoreExisting: true });
			let out = PathUtils.join(tmpDir, `${att.key}.pdf`);
			let backup = null;
			try {
				// okuma kopyası çift sayfaları böler: notlu/vurgulu PDF'te sayfa numaraları kaymasın
				let split = !(att.getAnnotations?.() || []).length;
				let r = await this.runMotor(level, path, out, split);
				let ocrPages = r.ocr || 0;
				let gain = r.once - r.sonra;
				let worthIt = ocrPages > 0 || gain >= Math.max(10 * 1024, r.once * 0.02);
				if (r.durum != "tamam" || !worthIt) {
					registry[att.libraryKey] = { size: before.size, level };
					await this.saveRegistry(registry);
					let message = r.durum == "tamam" || r.durum == "zaten-optimum" ? "zaten küçük" : (r.mesaj || r.durum);
					await this.log(`${title}: ${message}`);
					return { status: r.durum == "hata" ? "error" : "skip", message };
				}
				// motor çalışırken dosya değiştiyse (ör. okuyucuda sayfa döndürüldü) dokunma
				let now = await IOUtils.stat(path);
				if (now.size != before.size || now.lastModified != before.lastModified) {
					return { status: "skip", message: "dosya işlem sırasında değişti; atlandı" };
				}
				let data = await IOUtils.read(out);

				// 1) orijinalin kopyası  2) yeni içeriği atomik yaz  3) kopyayı Çöp Kutusu'na taşı
				let stem = PathUtils.filename(path).replace(/\.pdf$/i, "");
				backup = PathUtils.join(tmpDir, `${stem} (orijinal, Zotero ${att.key}).pdf`);
				await IOUtils.copy(path, backup);
				await IOUtils.write(path, data, { tmpPath: path + ".pdfkucult-tmp" });

				if (ocrPages > 0 || r.bolunen > 0) {
					// OCR ile metin eklendi ya da sayfalar bölündü: tam metin dizinini hemen yenile
					await Zotero.Fulltext.indexItems([att.id], { complete: true, ignoreErrors: true });
				}
				else {
					// Zotero'nun okuyucuda sayfa silerken/döndürürken yaptığı gibi: metin aynı olduğu
					// için yeniden dizinleme gerekmez; dosya yeni tarihli, senkronizasyon yükler.
					let mtime = Math.floor((await att.attachmentModificationTime) / 1000);
					att.attachmentLastProcessedModificationTime = mtime;
					await att.saveTx({ skipAll: true });
				}

				this.fileInfo.delete(att.id);
				let where = await this.moveToTrash(backup);
				backup = null;
				registry[att.libraryKey] = { size: data.byteLength, level, original: before.size,
					tarih: new Date().toISOString().slice(0, 10), ocr: ocrPages };
				await this.saveRegistry(registry);
				let ocrNote = ocrPages > 0 ? `; ${ocrPages} sayfaya OCR metni eklendi` : "";
				await this.log(`${title}: ${this.human(before.size)} → ${this.human(data.byteLength)} (${level})${ocrNote}; orijinal → ${where}`);
				return { status: "ok", before: before.size, after: data.byteLength, ocr: ocrPages };
			}
			finally {
				await IOUtils.remove(out, { ignoreAbsent: true });
				if (backup) {
					await IOUtils.remove(backup, { ignoreAbsent: true });
				}
			}
		}
		catch (e) {
			Zotero.logError(e);
			await this.log(`${title}: HATA ${e.message || e}`);
			return { status: "error", message: String(e.message || e).slice(0, 200) };
		}
	},

	/** Kurulu PDF Küçült motorunu bulur: { command, prefix } ya da null. */
	async engine() {
		if (this._engine !== undefined && this._engine && await IOUtils.exists(this._engine.command)) {
			return this._engine;
		}
		let cands = [];
		if (Zotero.isWin) {
			let local = Services.env.get("LOCALAPPDATA") || PathUtils.join(this.home, "AppData", "Local");
			cands.push({ command: PathUtils.join(local, "Programs", "PDF Kucult", "PDF Kucult.exe"), prefix: ["--motor"] });
		}
		else {
			let arch = Services.sysinfo.getProperty("arch") == "aarch64" ? "arm64" : "x86_64";
			for (let base of ["/Applications", PathUtils.join(this.home, "Applications")]) {
				for (let a of [arch, arch == "arm64" ? "x86_64" : "arm64"]) {
					cands.push({
						command: PathUtils.join(base, "PDF Küçült.app", "Contents", "Resources", "motor", a, "pdfkucult"),
						prefix: ["--motor"],
					});
				}
			}
			// geliştirme ortamı (kur.sh)
			let support = PathUtils.join(this.home, "Library", "Application Support", "PDFKucult");
			cands.push({
				command: PathUtils.join(support, "venv", "bin", "python"),
				prefix: ["-I", PathUtils.join(support, "motor.py")],
				needs: PathUtils.join(support, "motor.py"),
			});
		}
		for (let c of cands) {
			if (await IOUtils.exists(c.command) && (!c.needs || await IOUtils.exists(c.needs))) {
				this._engine = c;
				return c;
			}
		}
		this._engine = null;
		return null;
	},

	async exec(args, { track = false } = {}) {
		const { Subprocess } = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
		let eng = await this.engine();
		let proc = await Subprocess.call({
			command: eng.command,
			arguments: [...eng.prefix, ...args],
			environment: { PATH: "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" },
			environmentAppend: true,
			stderr: "pipe",
		});
		if (track) {
			this.currentProc = proc;
		}
		let readAll = async (pipe) => {
			let s = "", chunk;
			while ((chunk = await pipe.readString())) {
				s += chunk;
			}
			return s;
		};
		try {
			let [stdout, stderr] = await Promise.all([readAll(proc.stdout), readAll(proc.stderr)]);
			let { exitCode } = await proc.wait();
			return { exitCode, stdout, stderr };
		}
		finally {
			if (track) {
				this.currentProc = null;
			}
		}
	},

	async runMotor(level, src, out, split = true) {
		let resultFile = out + ".json";
		await IOUtils.remove(resultFile, { ignoreAbsent: true });
		try {
			let { exitCode, stderr } = await this.exec(
				["--json", "--sessiz", "--sonuc", resultFile, "-s", level, ...(this.ocrOn() ? [] : ["--ocr-yok"]),
					...(split ? [] : ["--bolme-yok"]), "-o", out, src],
				{ track: true });
			if (this.cancelled) {
				throw new Error("durduruldu");
			}
			let text = (await IOUtils.exists(resultFile)) ? await IOUtils.readUTF8(resultFile) : "";
			let last = text.trim().split("\n").pop();
			try {
				return JSON.parse(last);
			}
			catch (e) {
				throw new Error(`motor çıkış kodu ${exitCode}: ${stderr.trim().split("\n").pop() || "sonuç yok"}`);
			}
		}
		finally {
			await IOUtils.remove(resultFile, { ignoreAbsent: true });
		}
	},

	async moveToTrash(path) {
		// Orijinal asla kalıcı silinmez: Çöp/Geri Dönüşüm Kutusu olmazsa ~/Zotero/pdfkucult-yedek'e konur
		try {
			if (!Zotero.isWin && await IOUtils.exists("/usr/bin/trash")) {
				const { Subprocess } = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
				let proc = await Subprocess.call({ command: "/usr/bin/trash", arguments: [path], stderr: "pipe" });
				await proc.wait();
			}
			if (await IOUtils.exists(path)) {
				await this.exec(["--cop-kutusuna", path]);
			}
		}
		catch (e) {
			Zotero.logError(e);
		}
		if (!(await IOUtils.exists(path))) {
			return Zotero.isWin ? "Geri Dönüşüm Kutusu" : "Çöp Kutusu";
		}
		let dir = PathUtils.join(Zotero.DataDirectory.dir, "pdfkucult-yedek");
		await IOUtils.makeDirectory(dir, { ignoreExisting: true });
		await IOUtils.move(path, PathUtils.join(dir, PathUtils.filename(path)));
		return dir;
	},

	// ------------------------------------------------------------ yardımcılar

	async loadRegistry() {
		try {
			return await IOUtils.readJSON(this.registryFile);
		}
		catch (e) {
			return {};
		}
	},

	async saveRegistry(registry) {
		await IOUtils.writeJSON(this.registryFile, registry, { tmpPath: this.registryFile + ".tmp" });
		this.buildStatus(registry);
	},

	// ------------------------------------------------------------ kazanç özeti

	async showSummary() {
		let win = Zotero.getMainWindow();
		let libraryID = win?.ZoteroPane?.getSelectedLibraryID() || Zotero.Libraries.userLibraryID;
		let library = Zotero.Libraries.get(libraryID);
		let registry = await this.loadRegistry();
		let rows = [];
		for (let [k, r] of Object.entries(registry)) {
			if (k.startsWith(`${libraryID}/`) && r.original && r.size) {
				rows.push({ key: k.split("/")[1], before: r.original, after: r.size });
			}
		}
		if (!rows.length) {
			this.notify(`“${library.name}” kütüphanesinde henüz küçültülmüş PDF yok.`);
			return;
		}
		let before = rows.reduce((s, r) => s + r.before, 0);
		let after = rows.reduce((s, r) => s + r.after, 0);
		rows.sort((a, b) => (b.before - b.after) - (a.before - a.after));
		let top = rows.slice(0, 10).map((r, i) => {
			let att = Zotero.Items.getByLibraryAndKey(libraryID, r.key);
			let title = att ? this.titleOf(att) : r.key;
			let diff = r.after <= r.before ? `−${this.human(r.before - r.after)}` : `+${this.human(r.after - r.before)}, OCR eklendi`;
			return `${i + 1}. ${title}\n     ${this.human(r.before)} → ${this.human(r.after)} (${diff})`;
		});
		Services.prompt.alert(win, "PDF Küçült — kazanç özeti",
			`“${library.name}” kütüphanesinde ${rows.length} PDF küçültüldü:\n`
			+ `${this.human(before)} → ${this.human(after)}  (%${Math.round(100 * (1 - after / before))} küçüldü, `
			+ `${this.human(before - after)} kazanıldı)\n\nEn çok yer kazandıranlar:\n${top.join("\n")}\n\n`
			+ "Makale bazında görmek için öğe listesinin sütun başlığına sağ tıklayıp “Kazanç” sütununu açın.");
	},

	// ------------------------------------------------------------ kütüphane taraması

	async scanLibrary() {
		let win = Zotero.getMainWindow();
		let libraryID = win?.ZoteroPane?.getSelectedLibraryID() || Zotero.Libraries.userLibraryID;
		let library = Zotero.Libraries.get(libraryID);
		let pw = new Zotero.ProgressWindow({ closeOnClick: false });
		pw.changeHeadline(`PDF Küçült — “${library.name}” taranıyor`);
		pw.show();
		let line = new pw.ItemProgress("attachmentPDF", "PDF'ler listeleniyor…");
		try {
			let items = await Zotero.Items.getAll(libraryID, false, false);
			let atts = items.filter(a => !a.deleted && a.isPDFAttachment() && a.isFileAttachment());
			let registry = await this.loadRegistry();
			let pending = [];
			this.missing = new Set();
			for (let [i, att] of atts.entries()) {
				if (i % 50 == 0) {
					line.setText(`${i}/${atts.length} PDF kontrol edildi`);
					line.setProgress(Math.round(50 * i / atts.length));
				}
				let path = await att.getFilePathAsync();
				if (!path || !(await IOUtils.exists(path))) {
					this.missing.add(att.libraryKey);
					continue;
				}
				let size = (await IOUtils.stat(path)).size;
				let rec = registry[att.libraryKey];
				if (rec && rec.size == size) {
					continue;  // bu bilgisayarda işlenmiş, dosya değişmemiş
				}
				pending.push({ att, path, size });
			}
			// başka bilgisayarda ya da masaüstü uygulamasıyla küçültülmüş mü? (PDF içindeki işaret)
			if (pending.length && await this.engine()) {
				line.setText(`${pending.length} PDF'in içindeki işaret okunuyor…`);
				line.setProgress(60);
				let tmpDir = PathUtils.join(PathUtils.tempDir, "pdfkucult");
				await IOUtils.makeDirectory(tmpDir, { ignoreExisting: true });
				let list = PathUtils.join(tmpDir, "tarama-liste.txt");
				let out = PathUtils.join(tmpDir, "tarama-sonuc.jsonl");
				await IOUtils.writeUTF8(list, pending.map(p => p.path).join("\n"));
				await IOUtils.remove(out, { ignoreAbsent: true });
				await this.exec(["--isaret-oku", "--liste", list, "--sonuc", out]);
				let marks = new Map();
				if (await IOUtils.exists(out)) {
					for (let ln of (await IOUtils.readUTF8(out)).split("\n")) {
						try {
							let r = JSON.parse(ln);
							if (r.isaret) {
								marks.set(r.dosya, r.isaret);
							}
						}
						catch (e) {}
					}
				}
				for (let p of pending) {
					let mk = marks.get(p.path);
					if (mk) {
						let level = mk.seviye in this.RANK ? mk.seviye : "onerilen";
						registry[p.att.libraryKey] = { size: p.size, level, isaret: mk.surum };
						if (mk.orijinal && mk.orijinal > 0) {
							registry[p.att.libraryKey].original = mk.orijinal;
						}
						p.done = true;
					}
				}
			}
			await this.saveRegistry(registry);
			let todo = pending.filter(p => !p.done);
			let total = todo.reduce((s, p) => s + p.size, 0);
			line.setProgress(100);
			line.setText(`${atts.length} PDF tarandı`);
			pw.startCloseTimer(1500);

			if (!todo.length) {
				this.notify(`“${library.name}” kütüphanesindeki bütün PDF'ler küçültülmüş ✓`
					+ (this.missing.size ? `\n(${this.missing.size} ekin dosyası bu bilgisayarda yok)` : ""));
				return;
			}
			let ps = Services.prompt;
			let flags = ps.BUTTON_POS_0 * ps.BUTTON_TITLE_IS_STRING + ps.BUTTON_POS_1 * ps.BUTTON_TITLE_IS_STRING
				+ ps.BUTTON_POS_2 * ps.BUTTON_TITLE_IS_STRING + ps.BUTTON_POS_0_DEFAULT;
			let choice = ps.confirmEx(win, "PDF Küçült — tarama sonucu",
				`“${library.name}” kütüphanesinde ${todo.length} PDF küçültülmemiş (toplam ${this.human(total)}).`
				+ (this.missing.size ? `\n${this.missing.size} ekin dosyası bu bilgisayarda yok; onlar sayılmadı.` : "")
				+ "\n\n“Zotero'da göster” bu PDF'lerin makalelerini seçili hale getirir; öğe listesindeki "
				+ "“Küçültme” sütunundan da görebilirsiniz.",
				flags, "Hepsini akıllı sıkıştır", "Kapat", "Zotero'da göster", null, {});
			if (choice == 0) {
				todo.sort((a, b) => b.size - a.size);
				this.runBatch(todo.map(p => p.att), this.LEVEL, `Akıllı sıkıştırma — ${todo.length} küçültülmemiş PDF`);
			}
			else if (choice == 2) {
				let ids = [...new Set(todo.map(p => p.att.parentItemID || p.att.id))];
				await win.ZoteroPane.selectItems(ids, { inLibraryRoot: true });
			}
		}
		catch (e) {
			Zotero.logError(e);
			line.setError();
			line.setText("Tarama başarısız: " + (e.message || e));
			pw.startCloseTimer(8000);
		}
	},

	async log(line) {
		try {
			await IOUtils.makeDirectory(PathUtils.parent(this.logFile), { ignoreExisting: true });
			let stamp = new Date().toLocaleString("tr-TR");
			await IOUtils.writeUTF8(this.logFile, `${stamp}  ${line}\n`, { mode: "appendOrCreate" });
		}
		catch (e) {
			Zotero.logError(e);
		}
	},

	async openLog() {
		if (!(await IOUtils.exists(this.logFile))) {
			this.notify("Henüz kayıt yok.");
			return;
		}
		Zotero.launchFile(this.logFile);
	},

	titleOf(att) {
		let parent = att.parentItem;
		let t = (parent || att).getDisplayTitle() || att.attachmentFilename || "PDF";
		return t.length > 70 ? t.slice(0, 67) + "…" : t;
	},

	human(n) {
		if (n < 1024 * 1024) {
			return `${Math.max(1, Math.round(n / 1024))} KB`;
		}
		return `${(n / 1024 / 1024).toFixed(1)} MB`;
	},

	notify(text, headline = "PDF Küçült") {
		let pw = new Zotero.ProgressWindow();
		pw.changeHeadline(headline);
		pw.addDescription(text);
		pw.show();
		pw.startCloseTimer(6000);
	},
};
