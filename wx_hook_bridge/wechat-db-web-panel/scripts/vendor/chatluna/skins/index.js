// Adapted from ChatLunaLab/chatluna-group-analysis @ 389d53b. See ../LICENSE.
"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ScrapbookSkinRenderer = exports.ArtSkinRenderer = exports.NewspaperSkinRenderer = exports.AnimeSkinRenderer = exports.Md3SkinRenderer = exports.getAvatarUrl = exports.skinRegistry = void 0;
const md3_1 = require("./md3");
const anime_1 = require("./anime");
const newspaper_1 = require("./newspaper");
const art_1 = require("./art");
const scrapbook_1 = require("./scrapbook");
class SkinRegistry {
    skins = new Map();
    constructor() {
        const renderers = [
            new md3_1.Md3SkinRenderer(),
            new anime_1.AnimeSkinRenderer(),
            new newspaper_1.NewspaperSkinRenderer(),
            new art_1.ArtSkinRenderer(),
            new scrapbook_1.ScrapbookSkinRenderer()
        ];
        for (const renderer of renderers)
            this.register(renderer);
    }
    register(skin) {
        this.skins.set(skin.id, skin);
    }
    getSafe(id) {
        return this.skins.get(id) || this.skins.get('md3');
    }
}
exports.skinRegistry = new SkinRegistry();
var types_1 = require("./types");
Object.defineProperty(exports, "getAvatarUrl", { enumerable: true, get: function () { return types_1.getAvatarUrl; } });
var md3_2 = require("./md3");
Object.defineProperty(exports, "Md3SkinRenderer", { enumerable: true, get: function () { return md3_2.Md3SkinRenderer; } });
var anime_2 = require("./anime");
Object.defineProperty(exports, "AnimeSkinRenderer", { enumerable: true, get: function () { return anime_2.AnimeSkinRenderer; } });
var newspaper_2 = require("./newspaper");
Object.defineProperty(exports, "NewspaperSkinRenderer", { enumerable: true, get: function () { return newspaper_2.NewspaperSkinRenderer; } });
var art_2 = require("./art");
Object.defineProperty(exports, "ArtSkinRenderer", { enumerable: true, get: function () { return art_2.ArtSkinRenderer; } });
var scrapbook_2 = require("./scrapbook");
Object.defineProperty(exports, "ScrapbookSkinRenderer", { enumerable: true, get: function () { return scrapbook_2.ScrapbookSkinRenderer; } });
