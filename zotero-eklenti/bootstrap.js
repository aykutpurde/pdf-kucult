/* PDF Küçült — Zotero eklentisi: yaşam döngüsü kancaları. Asıl iş pdfkucult.js içinde. */

var PDFKucult;

async function startup({ id, version, rootURI }, reason) {
	await Zotero.initializationPromise;
	Services.scriptloader.loadSubScript(rootURI + "pdfkucult.js");
	PDFKucult.init({ id, version, rootURI });
	for (let win of Zotero.getMainWindows()) {
		onMainWindowLoad({ window: win });
	}
}

function onMainWindowLoad({ window }) {
	window.MozXULElement.insertFTLIfNeeded("pdfkucult.ftl");
}

function onMainWindowUnload({ window }) {
	window.document.querySelector('link[href="pdfkucult.ftl"]')?.remove();
}

function shutdown() {
	PDFKucult?.shutdown();
	for (let win of Zotero.getMainWindows()) {
		onMainWindowUnload({ window: win });
	}
	PDFKucult = undefined;
}

function install() {}

function uninstall() {}
