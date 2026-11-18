'use strict';

const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');

function defaultSourceRoot() {
	const codeRoot = path.join(REPO_ROOT, 'code');
	if (fs.existsSync(codeRoot) && fs.statSync(codeRoot).isDirectory()) {
		return codeRoot;
	}
	return path.join(REPO_ROOT, 'src');
}

module.exports = {
	REPO_ROOT,
	defaultSourceRoot,
};
