import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ActivityIndicator, Alert, Image, Linking, Modal, Platform, Pressable, ScrollView, Share, StyleSheet, Text, View } from 'react-native'
import * as MediaLibrary from 'expo-media-library'
import * as FileSystem from 'expo-file-system/legacy'
import { WebView } from 'react-native-webview'
import type { WebView as WebViewType, WebViewMessageEvent } from 'react-native-webview'
import { useNavigation, useRoute } from '@react-navigation/native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import {
  getSaved, toggleSaved, saveContentCache, getContentCache, removeContentCache,
  type FeedItem,
} from '../localDb'
import { fetchHtml } from '../scraper/utils'
import { fetchOriconReadableContent } from '../scraper/oricon'
import { useTheme } from '../ThemeContext'
import { useLang } from '../LangContext'
import { useSettings, DEFAULT_FONT_SIZE } from '../hooks/useSettings'
import type { Theme } from '../theme'

export interface ReaderParams {
  url: string
  title: string | null
  id: string
  platform: string
}

interface ReaderImageAction {
  url: string
  alt: string | null
  pageUrl: string | null
  width?: number | null
  height?: number | null
}

const IMAGE_ACTION_LABELS: Record<string, { title: string; save: string; share: string; open: string; close: string }> = {
  ja: { title: '画像', save: '保存', share: '共有', open: '画像を開く', close: '閉じる' },
  en: { title: 'Image', save: 'Save', share: 'Share', open: 'Open image', close: 'Close' },
  'zh-TW': { title: '圖片', save: '儲存', share: '分享', open: '開啟圖片', close: '關閉' },
  'zh-CN': { title: '图片', save: '保存', share: '分享', open: '打开图片', close: '关闭' },
}

const AD_HOST_RE =
  /(^|\.)((2mdn|doubleclick|googlesyndication|googleadservices|adnxs|amazon-adsystem|criteo|outbrain|pubmatic|rubiconproject|scorecardresearch|taboola|teads)\.com|(criteo|doubleclick)\.net|adservice\.google\.com|googletagmanager\.com|google-analytics\.com|analytics\.yahoo\.com|yjtag\.yahoo\.co\.jp|yads\.c\.yimg\.jp|b92\.yahoo\.co\.jp|ad\.yahoo\.co\.jp|ad-stir\.com|ad-generation\.jp|admatrix\.jp|adingo\.jp|fam-ad\.com|fluct\.jp|genieessp\.jp|gmossp-sp\.jp|i-mobile\.co\.jp|im-apps\.net|impact-ad\.jp|microad\.jp|nend\.net|popin\.cc)$/i

const AD_PATH_RE =
  /\/(?:adserver|ads?|advert|advertisement|banner|sponsor|promoted)(?:[./?_-]|$)/i

const TRACKING_PARAM_RE = /^(utm_|fbclid$|gclid$|yclid$|mc_cid$|mc_eid$|igshid$|ref$)/i

function stripTrackingParams(rawUrl: string): string {
  try {
    const parsed = new URL(rawUrl)
    for (const key of Array.from(parsed.searchParams.keys())) {
      if (TRACKING_PARAM_RE.test(key)) parsed.searchParams.delete(key)
    }
    return parsed.toString()
  } catch {
    return rawUrl
  }
}

function shouldBlockReaderRequest(rawUrl: string): boolean {
  if (/^(about:|data:|blob:|file:|mailto:|tel:)/i.test(rawUrl)) return false
  try {
    const parsed = new URL(rawUrl)
    return AD_HOST_RE.test(parsed.hostname) || AD_PATH_RE.test(parsed.pathname)
  } catch {
    return AD_HOST_RE.test(rawUrl) || AD_PATH_RE.test(rawUrl)
  }
}

function normalize5chReaderUrl(rawUrl: string): string {
  try {
    const parsed = new URL(rawUrl)
    const isItest = /^itest\.5ch\.(net|io)$/i.test(parsed.hostname)
    const isFiveCh = /(^|\.)5ch\.(net|io)$/i.test(parsed.hostname)
    if (!isFiveCh && !isItest) {
      return rawUrl
    }
    const itestMatch = parsed.pathname.match(/^\/([^/]+)\/test\/read\.cgi\/([^/]+)\/(\d{9,})/)
    if (isItest && itestMatch) {
      return `https://itest.5ch.io/${itestMatch[1]}/test/read.cgi/${itestMatch[2]}/${itestMatch[3]}/`
    }
    const match = parsed.pathname.match(/\/test\/read\.cgi\/([^/]+)\/(\d{9,})/)
    if (!match) return rawUrl
    const hostParts = parsed.hostname.split('.')
    const server = hostParts.length > 2 ? hostParts[0] : ''
    if (!server || /^(www|itest|find|dig)$/i.test(server)) return rawUrl
    return `https://itest.5ch.io/${server}/test/read.cgi/${match[1]}/${match[2]}/`
  } catch {
    return rawUrl
  }
}

function readerUrlForPlatform(rawUrl: string, platform: string): string {
  const cleanUrl = stripTrackingParams(rawUrl)
  if (platform === '5ch') return normalize5chReaderUrl(cleanUrl)
  return cleanUrl
}

const AD_BLOCK_JS = `
(function () {
  var selectors = [
    'iframe[src*="doubleclick"]',
    'iframe[src*="googlesyndication"]',
    'iframe[src*="ad"]',
    'script[src*="doubleclick"]',
    'script[src*="googlesyndication"]',
    'script[src*="googletagmanager"]',
    'script[src*="google-analytics"]',
    'script[src*="microad"]',
    'script[src*="yads"]',
    'amp-ad',
    'amp-embed[type="taboola"]',
    '[id*="ad-"]',
    '[id^="ad_"]',
    '[id*="ads"]',
    '[class*=" ad-"]',
    '[class^="ad-"]',
    '[class*=" ads"]',
    '[class*="advert"]',
    '[class*="sponsor"]',
    '[aria-label*="広告"]',
    '[data-ad]',
    '[data-ad-unit]'
  ];
  selectors.push(
    'iframe[src*="googleads"]',
    'ins.adsbygoogle',
    '[id*="_ad_"]',
    '[id*="banner"]',
    '[class*="_ad_"]',
    '[class*="ads-"]',
    '[class*="banner"]',
    '[class*="promotion"]',
    '[class*="google-auto-placed"]',
    '[aria-label*="広告"]',
    '[data-google-query-id]',
    '#ad',
    '#ads',
    '#ad_header',
    '#ad_footer',
    '#ad_overlay',
    '#ad2',
    '#ad3',
    '#ad4',
    '#ad5',
    '#ad6',
    '.ad',
    '.ads',
    '.adbox',
    '.ad_box',
    '.ad_area',
    '.adsbygoogle',
    '.banner',
    '.banner_ad',
    '.adBanner',
    '.adContainer',
    '.ad-container'
  );
  var style = document.createElement('style');
  style.textContent = selectors.join(',') + '{display:none!important;visibility:hidden!important;opacity:0!important;height:0!important;max-height:0!important;min-height:0!important;margin:0!important;padding:0!important;overflow:hidden!important;} body{padding-bottom:0!important;}';
  (document.head || document.documentElement).appendChild(style);
  var blockedUrl = /(2mdn|doubleclick|googlesyndication|googleadservices|adservice\\.google|googletagmanager|google-analytics|amazon-adsystem|criteo|outbrain|pubmatic|rubiconproject|scorecardresearch|taboola|teads|analytics\\.yahoo|yjtag\\.yahoo|yads\\.c\\.yimg|b92\\.yahoo|ad\\.yahoo|ad-stir|ad-generation|admatrix|adingo|fam-ad|fluct|genieessp|gmossp|i-mobile|im-apps|impact-ad|microad|nend|popin|\\/adserver[\\/.?_-]|\\/ads?[\\/.?_-]|\\/advert|\\/banner|\\/sponsor|\\/promoted)/i;
  function isBlockedUrl(value) {
    try { return !!value && blockedUrl.test(String(value)); } catch (e) { return false; }
  }
  function removeNode(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }
  function elementUrl(el) {
    if (!el || !el.getAttribute) return '';
    return el.getAttribute('src') || el.getAttribute('href') || el.getAttribute('data-src') || el.getAttribute('data-href') || '';
  }
  function looksLikeAd(el) {
    var idClass = ((el.id || '') + ' ' + (el.className || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('data-ad') || '') + ' ' + (el.getAttribute('data-ad-unit') || '')).toLowerCase();
    if (/(^|[\\s_-])(ad|ads|adbox|ad-area|ad_area|adcontainer|advert|advertisement|banner|sponsor|sponsored|promotion|promoted)([\\s_-]|$)/.test(idClass)) return true;
    if (isBlockedUrl(elementUrl(el))) return true;
    var text = (el.innerText || '').trim();
    if (/^(\\u5e83\\u544a|sponsored|sponsor|PR)$/i.test(text)) return true;
    if (/^(広告|スポンサー|PR)$/i.test(text)) return true;
    var rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    if (rect && rect.height <= 360 && /(^|[\\s_-])(ad|ads|banner|sponsor|promoted)([\\s_-]|$)/.test(idClass)) return true;
    return false;
  }
  function guardElement(el) {
    if (!el || el.nodeType !== 1) return false;
    if (looksLikeAd(el)) {
      removeNode(el);
      return true;
    }
    if (el.querySelectorAll) {
      el.querySelectorAll('script,iframe,img,source,link,a,ins,amp-ad,amp-embed').forEach(function (child) {
        if (looksLikeAd(child)) removeNode(child);
      });
    }
    return false;
  }
  try {
    var originalAppendChild = Element.prototype.appendChild;
    Element.prototype.appendChild = function (node) {
      if (guardElement(node)) return node;
      return originalAppendChild.apply(this, arguments);
    };
    var originalInsertBefore = Element.prototype.insertBefore;
    Element.prototype.insertBefore = function (node) {
      if (guardElement(node)) return node;
      return originalInsertBefore.apply(this, arguments);
    };
    var originalSetAttribute = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (name, value) {
      if (/^(src|href|data-src|data-href)$/i.test(name) && isBlockedUrl(value)) {
        removeNode(this);
        return;
      }
      return originalSetAttribute.apply(this, arguments);
    };
  } catch (e) {}
  try {
    var originalOpen = window.open;
    window.open = function (target) {
      if (isBlockedUrl(target)) return null;
      return originalOpen ? originalOpen.apply(this, arguments) : null;
    };
  } catch (e) {}
  try {
    var originalFetch = window.fetch;
    if (originalFetch) {
      window.fetch = function (input) {
        var target = typeof input === 'string' ? input : input && input.url;
        if (isBlockedUrl(target)) return Promise.reject(new TypeError('Blocked by reader'));
        return originalFetch.apply(this, arguments);
      };
    }
  } catch (e) {}
  function clean() {
    selectors.forEach(function (selector) {
      document.querySelectorAll(selector).forEach(function (el) {
        removeNode(el);
      });
    });
    document.querySelectorAll('script, iframe, ins, amp-ad, amp-embed').forEach(function (el) {
      if (looksLikeAd(el)) removeNode(el);
    });
    document.querySelectorAll('[id*="ad-"],[id^="ad_"],[id*="_ad_"],[id*="ads"],[class*=" ad-"],[class^="ad-"],[class*=" ads"],[class*="_ad_"],[class*="advert"],[class*="banner"],[class*="sponsor"],[data-ad],[data-ad-unit],aside,section').forEach(function (el) {
      if (looksLikeAd(el)) removeNode(el);
    });
    document.querySelectorAll('*').forEach(function (el) {
      var s = window.getComputedStyle(el);
      if ((s.position === 'fixed' || s.position === 'sticky') && looksLikeAd(el)) removeNode(el);
    });
  }
  var pendingClean = null;
  function scheduleClean() {
    if (pendingClean) return;
    pendingClean = setTimeout(function () {
      pendingClean = null;
      clean();
    }, 100);
  }
  clean();
  setTimeout(clean, 250);
  setTimeout(clean, 1000);
  setTimeout(clean, 2500);
  new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      mutation.addedNodes.forEach(guardElement);
    });
    scheduleClean();
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
true;
`

const SAFE_AD_BLOCK_JS = `
(function () {
  var selectors = [
    'iframe[src*="doubleclick"]',
    'iframe[src*="googlesyndication"]',
    'iframe[src*="googleads"]',
    'script[src*="doubleclick"]',
    'script[src*="googlesyndication"]',
    'script[src*="googletagmanager"]',
    'script[src*="google-analytics"]',
    'script[src*="microad"]',
    'script[src*="yads"]',
    'ins.adsbygoogle',
    'amp-ad',
    'amp-embed[type="taboola"]',
    '[data-ad]',
    '[data-ad-client]',
    '[data-ad-slot]',
    '[data-ad-unit]',
    '[data-google-query-id]',
    '[id^="ad_"]',
    '[id^="ad-"]',
    '[id$="_ad"]',
    '[id$="-ad"]',
    '[class~="ad"]',
    '[class~="ads"]',
    '[class~="adbox"]',
    '[class~="advertisement"]',
    '[class~="adsbygoogle"]',
    '[class~="sponsored"]'
  ];
  var blockedUrl = /(2mdn|doubleclick|googlesyndication|googleadservices|adservice\\.google|googletagmanager|google-analytics|amazon-adsystem|criteo|outbrain|pubmatic|rubiconproject|scorecardresearch|taboola|teads|analytics\\.yahoo|yjtag\\.yahoo|yads\\.c\\.yimg|b92\\.yahoo|ad\\.yahoo|ad-stir|ad-generation|admatrix|adingo|fam-ad|fluct|genieessp|gmossp|i-mobile|im-apps|impact-ad|microad|nend|popin)/i;
  function isBlockedUrl(value) {
    try { return !!value && blockedUrl.test(String(value)); } catch (e) { return false; }
  }
  function removeNode(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }
  function elementUrl(el) {
    if (!el || !el.getAttribute) return '';
    return el.getAttribute('src') || el.getAttribute('href') || el.getAttribute('data-src') || el.getAttribute('data-href') || '';
  }
  function looksLikeAd(el) {
    if (!el || !el.getAttribute || /^(HTML|BODY|MAIN|ARTICLE)$/i.test(el.tagName || '')) return false;
    var idClass = ((el.id || '') + ' ' + (el.className || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('data-ad') || '') + ' ' + (el.getAttribute('data-ad-unit') || '')).toLowerCase();
    if (/(^|[\\s_-])(ad|ads|adbox|ad-area|ad_area|adcontainer|advertisement|adsbygoogle|sponsored)([\\s_-]|$)/.test(idClass)) return true;
    if (isBlockedUrl(elementUrl(el))) return true;
    var text = (el.innerText || '').trim();
    return /^(\\u5e83\\u544a|sponsored|sponsor|PR)$/i.test(text);
  }
  function clean(root) {
    var scope = root && root.querySelectorAll ? root : document;
    selectors.forEach(function (selector) {
      scope.querySelectorAll(selector).forEach(function (el) {
        if (looksLikeAd(el) || /^(SCRIPT|IFRAME|INS|AMP-AD|AMP-EMBED)$/i.test(el.tagName || '')) removeNode(el);
      });
    });
  }
  var style = document.createElement('style');
  style.textContent = selectors.join(',') + '{display:none!important;visibility:hidden!important;opacity:0!important;max-height:0!important;overflow:hidden!important;}';
  (document.head || document.documentElement).appendChild(style);
  clean(document);
  setTimeout(function () { clean(document); }, 500);
  setTimeout(function () { clean(document); }, 2000);
  new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      mutation.addedNodes.forEach(function (node) {
        if (node && node.nodeType === 1) {
          if (looksLikeAd(node)) removeNode(node);
          else clean(node);
        }
      });
    });
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
true;
`

const FIVECH_AD_BLOCK_JS = `
(function () {
  return true;

  var selectors = [
    'iframe',
    'ins.adsbygoogle',
    '[data-ad]',
    '[data-ad-client]',
    '[data-ad-slot]',
    '[data-google-query-id]',
    '[id^="ad_"]',
    '[id^="ad-"]',
    '[id*="_ad_"]',
    '[id*="-ad-"]',
    '[id*="ads"]',
    '[id*="banner"]',
    '[class~="ad"]',
    '[class~="ads"]',
    '[class~="adbox"]',
    '[class~="adsbygoogle"]',
    '[class*=" ad-"]',
    '[class^="ad-"]',
    '[class*="_ad_"]',
    '[class*="ads-"]',
    '[class*="banner"]',
    '[class*="sponsor"]',
    '[class*="promotion"]',
    '[class*="recommend-ad"]',
    'a[href*="googleadservices"]',
    'a[href*="doubleclick"]',
    'a[href*="adclick"]',
    'a[href*="/ad/"]',
    'a[href*="/ads/"]'
  ];
  var blockedUrl = /(2mdn|doubleclick|googlesyndication|googleadservices|adservice\\.google|adclick|adroute|ad-stir|ad-generation|admatrix|adingo|fam-ad|fluct|genieessp|gmossp|i-mobile|im-apps|impact-ad|microad|nend|popin|yads\\.c\\.yimg|ad\\.yahoo|b92\\.yahoo)/i;

  function removeNode(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function elementUrl(el) {
    if (!el || !el.getAttribute) return '';
    return el.getAttribute('src') || el.getAttribute('href') || el.getAttribute('data-src') || el.getAttribute('data-href') || '';
  }

  function isThreadContent(el) {
    var text = ((el.id || '') + ' ' + (el.className || '')).toLowerCase();
    return /(^|[\\s_-])(thread|res|reply|post|message|content|article|main|body)([\\s_-]|$)/.test(text);
  }

  function looksLike5chAd(el) {
    if (!el || !el.getAttribute || /^(HTML|BODY|MAIN|ARTICLE)$/i.test(el.tagName || '')) return false;
    if (isThreadContent(el)) return false;
    if (blockedUrl.test(elementUrl(el))) return true;
    var idClass = ((el.id || '') + ' ' + (el.className || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
    if (/(^|[\\s_-])(ad|ads|adbox|ad-area|ad_area|adcontainer|adsbygoogle|banner|sponsor|sponsored|promotion|pr)([\\s_-]|$)/.test(idClass)) return true;
    var text = (el.innerText || '').trim();
    if (/^(\\u5e83\\u544a|sponsored|sponsor|PR)$/i.test(text)) return true;
    var rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    var style = window.getComputedStyle ? window.getComputedStyle(el) : null;
    if (rect && style && (style.position === 'fixed' || style.position === 'sticky') && rect.height <= 180) return true;
    return false;
  }

  function clean(root) {
    var scope = root && root.querySelectorAll ? root : document;
    selectors.forEach(function (selector) {
      scope.querySelectorAll(selector).forEach(function (el) {
        if (looksLike5chAd(el) || /^(IFRAME|INS)$/i.test(el.tagName || '')) removeNode(el);
      });
    });
    scope.querySelectorAll('script,img,source,a').forEach(function (el) {
      if (blockedUrl.test(elementUrl(el))) removeNode(el);
    });
  }

  var style = document.createElement('style');
  style.textContent = selectors.join(',') + '{display:none!important;visibility:hidden!important;max-height:0!important;overflow:hidden!important;}';
  (document.head || document.documentElement).appendChild(style);
  clean(document);
  setTimeout(function () { clean(document); }, 500);
  setTimeout(function () { clean(document); }, 1500);
  setTimeout(function () { clean(document); }, 3500);
  new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      mutation.addedNodes.forEach(function (node) {
        if (node && node.nodeType === 1) {
          if (looksLike5chAd(node)) removeNode(node);
          else clean(node);
        }
      });
    });
  }).observe(document.documentElement, { childList: true, subtree: true });
  return true;
})();
true;
`

// ── Content extraction ──────────────────────────────────────────────────────

const FIVECH_PAGE_FIX_JS = `
(function () {
  if (window.__OTTERPIA_5CH_FIX__) return true;
  window.__OTTERPIA_5CH_FIX__ = true;
  var style = document.createElement('style');
  style.textContent = [
    'html,body{min-width:0!important;width:auto!important;max-width:100%!important;overflow-x:hidden!important;}',
    'body{font-size:16px!important;line-height:1.55!important;-webkit-text-size-adjust:100%!important;}',
    'main,article,#thread,#contents,#content,.thread,.res,.post,.message,dl,dt,dd{max-width:100%!important;box-sizing:border-box!important;}',
    'img,video,iframe{max-width:100%!important;height:auto!important;}',
    'pre,code,.aa{white-space:pre-wrap!important;word-break:break-word!important;overflow-wrap:anywhere!important;}',
    '[style*="position: fixed"],[style*="position:fixed"]{max-width:100%!important;}',
    '.ad,.ads,.adbox,.adsbygoogle,[id^="ad_"],[id^="ad-"],[data-ad],[data-google-query-id],iframe[src*="doubleclick"],iframe[src*="googlesyndication"]{display:none!important;}'
  ].join('\\n');
  (document.head || document.documentElement).appendChild(style);
  function removeBadOverlays() {
    document.querySelectorAll('iframe[src*="doubleclick"],iframe[src*="googlesyndication"],ins.adsbygoogle,.adsbygoogle,[data-ad],[data-google-query-id]').forEach(function (el) {
      if (el && el.parentNode) el.parentNode.removeChild(el);
    });
  }
  removeBadOverlays();
  setTimeout(removeBadOverlays, 500);
  setTimeout(removeBadOverlays, 1500);
  return true;
})();
true;
`

const IMAGE_ACTION_JS = `
(function () {
  if (window.__OTTERPIA_IMAGE_ACTIONS__) return true;
  window.__OTTERPIA_IMAGE_ACTIONS__ = true;
  var longPressTimer = null;
  var startX = 0;
  var startY = 0;
  var pendingTarget = null;

  function srcFromSrcset(value) {
    if (!value) return '';
    var parts = String(value).split(',').map(function (part) {
      return part.trim().split(/\\s+/)[0];
    }).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : '';
  }

  function absoluteUrl(value) {
    if (!value) return '';
    try { return new URL(value, document.baseURI).toString(); } catch (e) { return String(value); }
  }

  function backgroundImageUrl(el) {
    try {
      var style = window.getComputedStyle(el);
      var match = style && style.backgroundImage && style.backgroundImage.match(/url\\((["']?)(.*?)\\1\\)/);
      return match ? match[2] : '';
    } catch (e) {
      return '';
    }
  }

  function imageCandidate(target) {
    var el = target;
    var depth = 0;
    while (el && el.nodeType === 1 && depth < 8) {
      var tag = (el.tagName || '').toUpperCase();
      if (tag === 'IMG') {
        return {
          url: el.currentSrc || el.src || el.getAttribute('src') || el.getAttribute('data-src') || el.getAttribute('data-original') || el.getAttribute('data-lazy-src') || srcFromSrcset(el.getAttribute('srcset') || el.getAttribute('data-srcset')),
          alt: el.getAttribute('alt') || el.getAttribute('title') || ''
        };
      }
      if (tag === 'VIDEO') {
        var poster = el.getAttribute('poster');
        if (poster) return { url: poster, alt: el.getAttribute('title') || '' };
      }
      var bgUrl = backgroundImageUrl(el);
      if (bgUrl && bgUrl !== 'none') {
        return { url: bgUrl, alt: el.getAttribute('aria-label') || el.getAttribute('title') || '' };
      }
      el = el.parentElement;
      depth += 1;
    }
    return null;
  }

  function postImage(target) {
    var found = imageCandidate(target);
    if (!found) return false;
    var imageUrl = absoluteUrl(found.url);
    if (!/^https?:\\/\\//i.test(imageUrl)) return false;
    window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify({
      type: 'otterpia:image-action',
      url: imageUrl,
      alt: found.alt || document.title || '',
      pageUrl: location.href
    }));
    return true;
  }

  function clearLongPress() {
    if (longPressTimer) clearTimeout(longPressTimer);
    longPressTimer = null;
    pendingTarget = null;
  }

  document.addEventListener('contextmenu', function (event) {
    if (postImage(event.target)) event.preventDefault();
  }, true);

  // Single-tap on any <img> → show save dialog (prevents link navigation for images)
  document.addEventListener('click', function (event) {
    var el = event.target;
    var depth = 0;
    while (el && el.nodeType === 1 && depth < 6) {
      if ((el.tagName || '').toUpperCase() === 'IMG') {
        var found = imageCandidate(el);
        if (!found) return;
        var imageUrl = absoluteUrl(found.url);
        if (!/^https?:\/\//i.test(imageUrl)) return;
        event.preventDefault();
        event.stopPropagation();
        window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify({
          type: 'otterpia:image-action',
          url: imageUrl,
          alt: found.alt || document.title || '',
          pageUrl: location.href
        }));
        return;
      }
      el = el.parentElement;
      depth += 1;
    }
  }, true);

  document.addEventListener('touchstart', function (event) {
    if (!event.touches || event.touches.length !== 1) return;
    if (!imageCandidate(event.target)) return;
    pendingTarget = event.target;
    startX = event.touches[0].clientX;
    startY = event.touches[0].clientY;
    longPressTimer = setTimeout(function () {
      postImage(pendingTarget);
      clearLongPress();
    }, 500);
  }, { capture: true, passive: true });

  document.addEventListener('touchmove', function (event) {
    if (!longPressTimer || !event.touches || event.touches.length !== 1) return;
    var dx = Math.abs(event.touches[0].clientX - startX);
    var dy = Math.abs(event.touches[0].clientY - startY);
    if (dx > 20 || dy > 20) clearLongPress();
  }, { capture: true, passive: true });

  document.addEventListener('touchend', clearLongPress, true);
  document.addEventListener('touchcancel', clearLongPress, true);
  return true;
})();
true;
`

const READER_AD_BLOCK_JS = `${SAFE_AD_BLOCK_JS}
${FIVECH_AD_BLOCK_JS}`

const READER_INJECTED_JS = `${READER_AD_BLOCK_JS}
${IMAGE_ACTION_JS}`

const FIVECH_READER_INJECTED_JS = `${FIVECH_PAGE_FIX_JS}
${IMAGE_ACTION_JS}`

// Injects the Google Translate widget to translate the page in-place (no redirect).
// targetLang: Google Translate language code e.g. 'en', 'ja', 'zh-CN', 'zh-TW'
function makeTranslateJS(targetLang: string): string {
  return `
(function() {
  if (window.__ottTrDone) return;
  window.__ottTrDone = true;
  var style = document.createElement('style');
  style.textContent = [
    '.goog-te-banner-frame{display:none!important}',
    '.skiptranslate{display:none!important}',
    'body{top:0!important;position:static!important}',
    '#goog-gt-tt,#goog-gt-,#goog-gt-p{display:none!important}',
  ].join('');
  document.head.appendChild(style);
  var el = document.createElement('div');
  el.id = '__ott_tr_root';
  el.style.cssText = 'position:absolute;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none';
  document.body.appendChild(el);
  window.googleTranslateElementInit = function() {
    try {
      new window.google.translate.TranslateElement({
        pageLanguage: 'auto',
        includedLanguages: '${targetLang}',
        autoDisplay: false,
        multilanguagePage: false,
      }, '__ott_tr_root');
      var attempt = 0;
      var iv = setInterval(function() {
        attempt++;
        var sel = document.querySelector('.goog-te-combo');
        if (sel) {
          sel.value = '${targetLang}';
          sel.dispatchEvent(new Event('change'));
          clearInterval(iv);
        } else if (attempt > 20) {
          clearInterval(iv);
        }
      }, 200);
    } catch(e) {}
  };
  var s = document.createElement('script');
  s.src = 'https://translate.googleapis.com/translate_a/element.js?cb=googleTranslateElementInit';
  document.head.appendChild(s);
})();
true;
`
}

function extractReadableContent(html: string): string {
  const cleaned = html
    .replace(/<script\b[\s\S]*?<\/script>/gi, '')
    .replace(/<style\b[\s\S]*?<\/style>/gi, '')
    .replace(/<noscript\b[\s\S]*?<\/noscript>/gi, '')
    .replace(/<iframe\b[\s\S]*?<\/iframe>/gi, '')
    .replace(/<svg\b[\s\S]*?<\/svg>/gi, '')
    .replace(/<!--[\s\S]*?-->/g, '')

  return (
    cleaned.match(/<article\b[^>]*>([\s\S]*?)<\/article>/i)?.[1] ??
    cleaned.match(/<main\b[^>]*>([\s\S]*?)<\/main>/i)?.[1] ??
    cleaned.match(/<div[^>]+(?:class|id)="[^"]*(?:article|content|post|entry|text|body|story)[^"]*"[^>]*>([\s\S]*?)<\/div>/i)?.[1] ??
    cleaned.match(/<body\b[^>]*>([\s\S]*?)<\/body>/i)?.[1] ??
    cleaned
  )
}

function wrapHtml(content: string, title: string | null, isDark: boolean, fontSize = DEFAULT_FONT_SIZE): string {
  const bg    = isDark ? '#111111' : '#fafafa'
  const text  = isDark ? '#e8e8e8' : '#1a1a1a'
  const link  = isDark ? '#a78bfa' : '#7C3AED'
  const sub   = isDark ? '#2c2c2e' : '#e5e7eb'
  const muted = isDark ? '#6b7280' : '#9ca3af'
  return `<!DOCTYPE html><html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=2">
<style>
*{box-sizing:border-box}
body{background:${bg};color:${text};font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:${fontSize}px;line-height:1.8;padding:16px;max-width:760px;margin:0 auto;word-break:break-word}
h1,h2,h3,h4{line-height:1.3;margin:1.2em 0 .5em}
h1{font-size:1.4em}h2{font-size:1.2em}h3{font-size:1.1em}
p{margin:.8em 0}
a{color:${link};text-decoration:none}
img{max-width:100%;height:auto;border-radius:6px;margin:8px 0;display:block}
figure{margin:.8em 0}figcaption{font-size:13px;color:${muted};margin-top:4px}
blockquote{border-left:3px solid ${link};padding:.5em 1em;margin:1em 0;opacity:.85}
pre{background:${sub};border-radius:6px;padding:12px;overflow-x:auto;font-size:14px;line-height:1.5}
code{font-family:monospace;font-size:.9em;background:${sub};border-radius:3px;padding:1px 5px}
pre code{background:none;padding:0}
hr{border:none;border-top:1px solid ${sub};margin:1.5em 0}
table{width:100%;border-collapse:collapse;display:block;overflow-x:auto;margin:1em 0}
th,td{border:1px solid ${sub};padding:8px;text-align:left;vertical-align:top}
th{background:${sub}}
nav,header,footer,[role=navigation],[role=banner],[role=contentinfo],.nav,.menu,.header,.footer,.sidebar,.ad,.ads,.adbox,.ad_box,.ad_area,.adsbygoogle,.advert,.advertisement,.banner,.sponsor,.sponsored,.promotion,[id*="ad-"],[id^="ad_"],[id*="_ad_"],[id*="ads"],[class*=" ad-"],[class^="ad-"],[class*=" ads"],[class*="_ad_"],[class*="advert"],[class*="banner"],[class*="sponsor"],[data-ad],[data-ad-unit]{display:none!important}
</style></head><body>${content}</body></html>`
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// ── Component ───────────────────────────────────────────────────────────────

function makeStyles(t: Theme) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: t.bg },
    webview: { flex: 1 },
    overlay: {
      position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
      justifyContent: 'center', alignItems: 'center',
      backgroundColor: 'transparent',
    },
    progressTrack: {
      position: 'absolute', top: 0, left: 0, right: 0, height: 2,
      backgroundColor: 'transparent', zIndex: 4,
    },
    progressFill: { height: 2, backgroundColor: t.primary },
    // ── Top bar ──────────────────────────────────────────────────────────────
    topBar: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: t.card,
      paddingTop: Platform.OS === 'ios' ? 52 : 10,
      paddingBottom: 10, paddingHorizontal: 8,
      borderBottomWidth: 1, borderColor: t.divider, gap: 6,
    },
    topBackBtn: {
      width: 36, height: 36, borderRadius: 18,
      alignItems: 'center', justifyContent: 'center',
    },
    topTitleWrap: { flex: 1 },
    topTitle: { fontSize: 14, fontWeight: '700', color: t.text, lineHeight: 19 },
    topUrl: { fontSize: 10, color: t.textMuted, marginTop: 1 },
    topSaveBtn: {
      width: 36, height: 36, borderRadius: 18,
      alignItems: 'center', justifyContent: 'center',
      backgroundColor: t.divider,
    },
    topSaveBtnOn: { backgroundColor: t.primaryBg },
    // ── Offline / reader banner ───────────────────────────────────────────────
    offlineBanner: {
      backgroundColor: t.primaryBg, paddingHorizontal: 14, paddingVertical: 6,
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6,
    },
    offlineText: { fontSize: 12, color: t.primary, fontWeight: '600' },
    // ── Error view ────────────────────────────────────────────────────────────
    errorView: {
      flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32, gap: 16,
    },
    errorEmoji: { fontSize: 48 },
    errorTitle: { fontSize: 16, fontWeight: '700', color: t.text, textAlign: 'center' },
    errorBody: { fontSize: 13, color: t.textMuted, textAlign: 'center', lineHeight: 20 },
    errorBtn: {
      backgroundColor: t.primary, paddingHorizontal: 20, paddingVertical: 10,
      borderRadius: 10, marginTop: 8,
    },
    errorBtnText: { fontSize: 14, fontWeight: '700', color: '#fff' },
    // ── Bottom toolbar ────────────────────────────────────────────────────────
    bottomBar: {
      position: 'absolute', bottom: 24, left: 16, right: 16,
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-around',
      backgroundColor: t.card,
      paddingHorizontal: 6, paddingVertical: 8,
      borderRadius: 30,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.12,
      shadowRadius: 14,
      elevation: 8,
    },
    navBtn: {
      width: 36, height: 36, borderRadius: 18,
      alignItems: 'center', justifyContent: 'center',
      backgroundColor: t.divider,
    },
    navBtnOff: { opacity: 0.32 },
    barBtn: {
      width: 36, height: 36, borderRadius: 18,
      alignItems: 'center', justifyContent: 'center',
      backgroundColor: t.divider,
    },
    barBtnActive: { backgroundColor: t.primaryBg },
    barBtnDisabled: { opacity: 0.35 },
    barDivider: { width: 1, height: 22, backgroundColor: t.divider, marginHorizontal: 2 },
    imageSheetBackdrop: {
      flex: 1, justifyContent: 'flex-end',
      backgroundColor: 'rgba(0,0,0,0.38)',
    },
    imageSheet: {
      backgroundColor: t.card, padding: 16, paddingBottom: 24,
      borderTopLeftRadius: 18, borderTopRightRadius: 18, gap: 12,
      borderWidth: 1, borderColor: t.border,
    },
    gallerySheet: {
      backgroundColor: t.card, padding: 14, paddingBottom: 22,
      borderTopLeftRadius: 18, borderTopRightRadius: 18, gap: 12,
      borderWidth: 1, borderColor: t.border,
      maxHeight: '78%',
    },
    galleryHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
    galleryTitle: { flex: 1, fontSize: 15, fontWeight: '800', color: t.text },
    galleryCount: { fontSize: 12, color: t.textMuted, fontWeight: '700' },
    galleryActions: { flexDirection: 'row', gap: 8 },
    galleryGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, paddingBottom: 6 },
    galleryThumbBtn: {
      width: '31%', aspectRatio: 1, borderRadius: 10, overflow: 'hidden',
      backgroundColor: t.divider, borderWidth: 1, borderColor: t.border,
    },
    galleryThumb: { width: '100%', height: '100%' },
    galleryThumbSelected: { borderWidth: 2, borderColor: t.primary },
    galleryCheckOverlay: {
      position: 'absolute', top: 4, right: 4,
      backgroundColor: 'rgba(0,0,0,0.45)', borderRadius: 12,
    },
    galleryEmpty: { color: t.textMuted, fontSize: 13, lineHeight: 19, paddingVertical: 14, textAlign: 'center' },
    imagePreview: {
      width: '100%', height: 240, borderRadius: 10,
      backgroundColor: t.divider,
    },
    imageTitle: { fontSize: 15, fontWeight: '700', color: t.text },
    imageUrl: { fontSize: 12, color: t.textMuted },
    imageActions: { flexDirection: 'row', gap: 8 },
    imageActionBtn: {
      flex: 1, alignItems: 'center', justifyContent: 'center',
      minHeight: 44, borderRadius: 12, backgroundColor: t.primary,
      paddingHorizontal: 10,
    },
    imageActionBtnAlt: { backgroundColor: t.divider },
    imageActionText: { color: '#fff', fontSize: 13, fontWeight: '700', textAlign: 'center' },
    imageActionTextAlt: { color: t.text },
  })
}

export default function ReaderScreen() {
  const navigation = useNavigation()
  const route = useRoute<any>()
  const { url, title, id, platform } = route.params as ReaderParams
  const readerUrl = useMemo(() => readerUrlForPlatform(url, platform), [platform, url])
  const isFiveChPage = platform === '5ch'

  const { theme, mode } = useTheme()
  const { t, lang } = useLang()
  const { settings } = useSettings()
  const DEEPL_LANG: Record<string, string> = { ja: 'ja', en: 'en', 'zh-CN': 'zh', 'zh-TW': 'zh-Hant' }
  const deeplTarget = DEEPL_LANG[lang] ?? 'en'
  const GT_LANG: Record<string, string> = { ja: 'ja', en: 'en', 'zh-CN': 'zh-CN', 'zh-TW': 'zh-TW' }
  const gtTarget = GT_LANG[lang] ?? 'en'
  const s = useMemo(() => makeStyles(theme), [theme])
  const qc = useQueryClient()

  const webRef = useRef<WebViewType>(null)
  const [webLoading, setWebLoading] = useState(true)
  const [loadProgress, setLoadProgress] = useState(0)
  const [webError, setWebError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [cachedContent, setCachedContent] = useState<string | null>(null)
  const [oriconContent, setOriconContent] = useState<string | null>(null)
  const [isTranslated, setIsTranslated] = useState(false)

  // Initialize translate state from persistent setting once loaded
  useEffect(() => {
    if (settings.autoTranslate) setIsTranslated(true)
  }, [settings.autoTranslate])
  const [webKey, setWebKey] = useState(0)
  const [canGoBack, setCanGoBack] = useState(false)
  const [canGoForward, setCanGoForward] = useState(false)
  const [currentUrl, setCurrentUrl] = useState(readerUrl)
  const [selectedImage, setSelectedImage] = useState<ReaderImageAction | null>(null)
  const [pageImages, setPageImages] = useState<ReaderImageAction[]>([])
  const [imageGalleryOpen, setImageGalleryOpen] = useState(false)
  const [selectedUrls, setSelectedUrls] = useState<Set<string>>(new Set())
  const [collectingImages, setCollectingImages] = useState(false)
  const [readerModeContent, setReaderModeContent] = useState<string | null>(null)
  const [loadingReaderMode, setLoadingReaderMode] = useState(false)
  const imageLabels = IMAGE_ACTION_LABELS[lang] ?? IMAGE_ACTION_LABELS.en

  const { data: saved = [] } = useQuery({ queryKey: ['saved'], queryFn: getSaved })
  const isSaved = saved.some(s => s.id === id)

  useEffect(() => {
    getContentCache(id).then(raw => {
      if (raw) setCachedContent(raw)
    })
  }, [id])

  useEffect(() => {
    if (platform !== 'oricon' || isTranslated || cachedContent || readerModeContent) {
      if (platform !== 'oricon') setOriconContent(null)
      return
    }
    let cancelled = false
    setOriconContent(null)
    setWebError(false)
    setWebLoading(true)
    fetchOriconReadableContent(url)
      .then(content => {
        if (cancelled) return
        setOriconContent(content)
        setLoadProgress(1)
      })
      .catch(e => {
        if (cancelled) return
        console.log('[reader:oricon] readable fetch failed, falling back to webview:', e)
        // Leave oriconContent null so the WebView loads the URL directly
      })
      .finally(() => {
        if (!cancelled) setWebLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [platform, url, webKey, isTranslated, cachedContent, readerModeContent])

  const handleSave = async () => {
    if (saving) return
    setSaving(true)
    try {
      const feedItem: FeedItem = {
        id, url: readerUrl, title: title ?? null, platform,
        content_text: null, author: null, thumbnail_url: null,
        media_type: 'article', published_at: new Date().toISOString(),
        watch_term_keyword: '', fetched_at: new Date().toISOString(),
      }
      const isNowSaved = await toggleSaved(feedItem)
      // Update UI immediately — don't wait for content caching
      qc.invalidateQueries({ queryKey: ['saved'] })
      setSaving(false)
      // Cache content in background (fire-and-forget)
      if (isNowSaved && !cachedContent) {
        fetchHtml(readerUrl)
          .then(html => {
            const content = extractReadableContent(html)
            setCachedContent(content)
            return saveContentCache(id, content)
          })
          .catch(e => console.log('[reader] cache failed:', e))
      } else if (!isNowSaved) {
        removeContentCache(id).catch(() => {})
        setCachedContent(null)
      }
    } catch (e) {
      console.log('[reader] save failed:', e)
      setSaving(false)
    }
  }

  const handleWebMessage = (event: WebViewMessageEvent) => {
    try {
      const payload = JSON.parse(event.nativeEvent.data) as {
        type?: string
        url?: string
        alt?: string
        pageUrl?: string
        images?: ReaderImageAction[]
      }
      if (payload.type === 'otterpia:images-found') {
        const images = Array.isArray(payload.images)
          ? payload.images.filter(img => img?.url && /^https?:\/\//i.test(img.url)).slice(0, 120)
          : []
        setPageImages(images)
        setImageGalleryOpen(true)
        setCollectingImages(false)
        return
      }
      if (payload.type !== 'otterpia:image-action' || !payload.url) return
      if (!/^https?:\/\//i.test(payload.url)) return
      setSelectedImage({
        url: payload.url,
        alt: payload.alt?.trim() || null,
        pageUrl: payload.pageUrl || null,
      })
    } catch {
      // Ignore unrelated WebView messages.
    }
  }

  const handleShareImage = async () => {
    if (!selectedImage) return
    try {
      await Share.share({
        title: selectedImage.alt || title || imageLabels.title,
        url: selectedImage.url,
        message: selectedImage.url,
      })
    } catch (e) {
      console.log('[reader] image share failed:', e)
      Alert.alert(t('errorAlert'), selectedImage.url)
    }
  }

  const handleOpenImage = () => {
    if (!selectedImage) return
    const imageUrl = selectedImage.url
    setSelectedImage(null)
    Linking.openURL(imageUrl).catch(e => {
      console.log('[reader] image open failed:', e)
      Alert.alert(t('errorAlert'), imageUrl)
    })
  }

  const handleSaveImage = async (imageUrl: string) => {
    try {
      const { status } = await MediaLibrary.requestPermissionsAsync()
      if (status !== 'granted') {
        Alert.alert(imageLabels.title, 'Photo library permission is required to save images.')
        return
      }
      const rawName = imageUrl.split('/').pop()?.split('?')[0] ?? 'image'
      const ext = rawName.split('.').pop()?.toLowerCase() ?? ''
      const filename = /^(jpg|jpeg|png|gif|webp)$/.test(ext) ? rawName : rawName + '.jpg'
      const dest = FileSystem.cacheDirectory + filename
      const { uri } = await FileSystem.downloadAsync(imageUrl, dest)
      await MediaLibrary.saveToLibraryAsync(uri)
      Alert.alert(imageLabels.save, imageLabels.save + ' ✓')
    } catch (e) {
      console.log('[reader] save image failed:', e)
      Alert.alert(t('errorAlert'), 'Failed to save image.')
    }
  }

  const collectPageImages = () => {
    setCollectingImages(true)
    webRef.current?.injectJavaScript(`
      (function () {
        function srcFromSrcset(value) {
          if (!value) return '';
          var parts = String(value).split(',').map(function (part) {
            return part.trim().split(/\\s+/)[0];
          }).filter(Boolean);
          return parts.length ? parts[parts.length - 1] : '';
        }
        function absoluteUrl(value) {
          if (!value) return '';
          try { return new URL(value, document.baseURI).toString(); } catch (e) { return String(value); }
        }
        function backgroundImageUrl(el) {
          try {
            var style = window.getComputedStyle(el);
            var match = style && style.backgroundImage && style.backgroundImage.match(/url\\((["']?)(.*?)\\1\\)/);
            return match ? match[2] : '';
          } catch (e) { return ''; }
        }
        var seen = {};
        var images = [];
        var MIN_SIZE = 100;
        function add(url, alt, width, height) {
          var absolute = absoluteUrl(url);
          if (!/^https?:\\/\\//i.test(absolute) || seen[absolute]) return;
          if ((width && width < MIN_SIZE) || (height && height < MIN_SIZE)) return;
          seen[absolute] = true;
          images.push({
            url: absolute,
            alt: alt || document.title || '',
            pageUrl: location.href,
            width: width || null,
            height: height || null
          });
        }
        // Selectors for junk zones to exclude
        var EXCLUDE_SELECTORS = [
          'nav', 'header', 'footer', 'aside',
          '[role="navigation"]', '[role="banner"]', '[role="complementary"]', '[role="contentinfo"]',
          '.nav', '.navbar', '.header', '.footer', '.sidebar', '.side-bar',
          '.related', '.recommend', '.ranking', '.trending', '.popular',
          '.ad', '.ads', '.advertisement', '.promo', '.promotion',
          '.widget', '.breadcrumb', '.pagination', '.comment', '.share',
          '.topics', '.pickup', '.special', '.category-list',
        ].join(',');
        function isInJunk(el) {
          var cur = el;
          while (cur && cur !== document.body) {
            if (cur.matches && cur.matches(EXCLUDE_SELECTORS)) return true;
            cur = cur.parentElement;
          }
          return false;
        }
        // Find the best content root
        var contentRoot = (
          document.querySelector('article') ||
          document.querySelector('[role="main"]') ||
          document.querySelector('main') ||
          document.querySelector('.article-body,.article-content,.post-body,.post-content,.entry-body,.entry-content,.story-body,.news-body,.news-content,.body-text,.article__body,.article__content,.articleBody,.newsArticle,.article-detail,.kiji-body') ||
          null
        );
        var scope = contentRoot || document.body;
        scope.querySelectorAll('img').forEach(function (img) {
          if (!contentRoot && isInJunk(img)) return;
          add(
            img.currentSrc || img.src || img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-original') || img.getAttribute('data-lazy-src') || srcFromSrcset(img.getAttribute('srcset') || img.getAttribute('data-srcset')),
            img.getAttribute('alt') || img.getAttribute('title') || '',
            img.naturalWidth || img.width || 0,
            img.naturalHeight || img.height || 0
          );
        });
        scope.querySelectorAll('video[poster]').forEach(function (video) {
          if (!contentRoot && isInJunk(video)) return;
          add(video.getAttribute('poster'), video.getAttribute('title') || '', video.clientWidth || 0, video.clientHeight || 0);
        });
        scope.querySelectorAll('[style*="background-image"]').forEach(function (el) {
          if (!contentRoot && isInJunk(el)) return;
          var rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
          add(backgroundImageUrl(el), el.getAttribute('aria-label') || el.getAttribute('title') || '', rect ? rect.width : 0, rect ? rect.height : 0);
        });
        window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify({
          type: 'otterpia:images-found',
          images: images.slice(0, 120)
        }));
      })();
      true;
    `)
    setTimeout(() => setCollectingImages(false), 4000)
  }

  const handleShareAllImages = async () => {
    if (pageImages.length === 0) return
    const urls = pageImages.map(img => img.url).join('\\n')
    try {
      await Share.share({ title: title || imageLabels.title, message: urls })
    } catch (e) {
      console.log('[reader] share all images failed:', e)
      Alert.alert(t('errorAlert'), urls)
    }
  }

  const [savingAll, setSavingAll] = useState(false)
  const handleSaveAllImages = async () => {
    if (pageImages.length === 0 || savingAll) return
    setSavingAll(true)
    try {
      const { status } = await MediaLibrary.requestPermissionsAsync()
      if (status !== 'granted') {
        Alert.alert(imageLabels.title, 'Photo library permission is required.')
        return
      }
      let saved = 0
      for (const img of pageImages) {
        try {
          const rawName = img.url.split('/').pop()?.split('?')[0] ?? 'image'
          const ext = rawName.split('.').pop()?.toLowerCase() ?? ''
          const filename = /^(jpg|jpeg|png|gif|webp)$/.test(ext) ? rawName : rawName + '.jpg'
          const dest = FileSystem.cacheDirectory + filename
          const { uri } = await FileSystem.downloadAsync(img.url, dest)
          await MediaLibrary.saveToLibraryAsync(uri)
          saved++
        } catch {}
      }
      Alert.alert(imageLabels.save, `${saved} / ${pageImages.length} saved ✓`)
    } catch (e) {
      Alert.alert(t('errorAlert'), 'Failed to save images.')
    } finally {
      setSavingAll(false)
    }
  }

  const [savingSelected, setSavingSelected] = useState(false)
  const handleSaveSelected = async () => {
    if (selectedUrls.size === 0 || savingSelected) return
    setSavingSelected(true)
    try {
      const { status } = await MediaLibrary.requestPermissionsAsync()
      if (status !== 'granted') {
        Alert.alert(imageLabels.title, 'Photo library permission is required.')
        return
      }
      let saved = 0
      const total = selectedUrls.size
      for (const url of selectedUrls) {
        try {
          const rawName = url.split('/').pop()?.split('?')[0] ?? 'image'
          const ext = rawName.split('.').pop()?.toLowerCase() ?? ''
          const filename = /^(jpg|jpeg|png|gif|webp)$/.test(ext) ? rawName : rawName + '.jpg'
          const dest = FileSystem.cacheDirectory + filename
          const { uri } = await FileSystem.downloadAsync(url, dest)
          await MediaLibrary.saveToLibraryAsync(uri)
          saved++
        } catch {}
      }
      Alert.alert(imageLabels.save, `${saved} / ${total} saved ✓`)
      setSelectedUrls(new Set())
    } catch (e) {
      Alert.alert(t('errorAlert'), 'Failed to save images.')
    } finally {
      setSavingSelected(false)
    }
  }

  const handleRefresh = () => {
    setWebError(false)
    setWebLoading(true)
    setLoadProgress(0)
    if (cachedContent) setCachedContent(null)
    if (oriconContent) setOriconContent(null)
    if (readerModeContent) setReaderModeContent(null)
    setWebKey(key => key + 1)
    requestAnimationFrame(() => webRef.current?.reload())
  }

  const handleReaderMode = async () => {
    if (readerModeContent) { setReaderModeContent(null); return }
    setLoadingReaderMode(true)
    try {
      const html = await fetchHtml(currentUrl)
      const content = extractReadableContent(html)
      setReaderModeContent(content)
    } catch (e) {
      console.log('[reader] reader mode failed:', e)
      Alert.alert(t('errorAlert'), 'Could not extract article content.')
    } finally {
      setLoadingReaderMode(false)
    }
  }

  const handleGoBackInPage = () => {
    if (canGoBack) webRef.current?.goBack()
  }

  const handleGoForwardInPage = () => {
    if (canGoForward) webRef.current?.goForward()
  }

  useLayoutEffect(() => {
    navigation.setOptions({
      headerShown: false,
    })
  }, [])

  const localContent = cachedContent ?? oriconContent ?? readerModeContent ?? null
  const webSource = localContent
    ? { html: wrapHtml(localContent, title, mode === 'dark', settings.fontSize) }
    : isTranslated
      ? { uri: `https://translate.google.com/translate?sl=auto&tl=${deeplTarget}&u=${encodeURIComponent(readerUrl)}` }
      : { uri: readerUrl }

  return (
    <View style={s.container}>

      {/* ── Top bar: title + back + bookmark ── */}
      <View style={s.topBar}>
        <Pressable onPress={() => navigation.goBack()} style={s.topBackBtn} hitSlop={10}>
          <Ionicons name="chevron-back" size={24} color={theme.primary} />
        </Pressable>
        <View style={s.topTitleWrap}>
          <Text style={s.topTitle} numberOfLines={2}>{title || url}</Text>
          {localContent
            ? <Text style={s.topUrl} numberOfLines={1}>
                {cachedContent ? t('offlineBanner') : readerModeContent ? '📖 Reader mode' : 'Oricon reader'}
              </Text>
            : <Text style={s.topUrl} numberOfLines={1}>{currentUrl || readerUrl}</Text>
          }
        </View>
        <Pressable
          onPress={handleSave}
          disabled={saving}
          style={[s.topSaveBtn, isSaved && s.topSaveBtnOn]}
          hitSlop={10}
        >
          <Ionicons
            name={saving ? 'hourglass-outline' : isSaved ? 'bookmark' : 'bookmark-outline'}
            size={22}
            color={isSaved ? theme.primary : theme.text}
          />
        </Pressable>
      </View>

      {webError ? (
        <View style={s.errorView}>
          <Text style={s.errorEmoji}>⚠️</Text>
          <Text style={s.errorTitle}>{t('pageErrorTitle')}</Text>
          <Text style={s.errorBody}>{t('pageErrorBody')}</Text>
          <Pressable style={s.errorBtn} onPress={handleRefresh}>
            <Text style={s.errorBtnText}>↺ 再試行</Text>
          </Pressable>
          <Pressable style={[s.errorBtn, { backgroundColor: 'transparent', borderWidth: 1, borderColor: theme.primary }]} onPress={() => Linking.openURL(readerUrl)}>
            <Text style={[s.errorBtnText, { color: theme.primary }]}>{t('openBrowser')}</Text>
          </Pressable>
        </View>
      ) : (
        <WebView
          key={`${webKey}:${isTranslated ? 'translated' : 'live'}:${cachedContent ? 'cached' : oriconContent ? 'oricon' : readerModeContent ? 'reader' : 'remote'}`}
          ref={webRef}
          source={webSource}
          style={s.webview}
          userAgent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
          domStorageEnabled
          javaScriptEnabled
          cacheEnabled
          thirdPartyCookiesEnabled={false}
          javaScriptCanOpenWindowsAutomatically={false}
          setSupportMultipleWindows={false}
          allowsBackForwardNavigationGestures
          startInLoadingState={false}
          onLoadStart={() => { setWebLoading(true); setLoadProgress(0.08); setWebError(false) }}
          onLoadProgress={event => setLoadProgress(event.nativeEvent.progress)}
          onLoadEnd={() => { setWebLoading(false); setLoadProgress(1) }}
          onNavigationStateChange={state => {
            setCanGoBack(state.canGoBack)
            setCanGoForward(state.canGoForward)
            if (state.url && !isTranslated) setCurrentUrl(state.url)
          }}
          onHttpError={e => { if (e.nativeEvent.statusCode >= 500) { setWebLoading(false); setWebError(true) } }}
          injectedJavaScript={isFiveChPage ? FIVECH_READER_INJECTED_JS : READER_INJECTED_JS}
          injectedJavaScriptBeforeContentLoaded={isFiveChPage ? FIVECH_PAGE_FIX_JS : READER_AD_BLOCK_JS}
          onMessage={handleWebMessage}
          onShouldStartLoadWithRequest={request => {
            // When translated, the page is loaded via Google Translate proxy. Only allow
            // Google Translate domain navigations — this blocks Yahoo News and other sites
            // from escaping the proxy frame via window.top.location redirects.
            if (isTranslated) {
              return /^https?:\/\/translate\.google(apis)?\.com\//i.test(request.url)
            }
            const requestUrl = stripTrackingParams(request.url)
            if (requestUrl === readerUrl) return true
            return !shouldBlockReaderRequest(request.url)
          }}
        />
      )}

      {webLoading && !webError && (
        <View style={s.progressTrack} pointerEvents="none">
          <View style={[s.progressFill, { width: `${Math.max(8, Math.round(loadProgress * 100))}%` }]} />
        </View>
      )}

      {webLoading && !webError && (
        <View style={s.overlay} pointerEvents="none">
          <ActivityIndicator color={theme.primary} size="large" />
        </View>
      )}

      {/* ── Image gallery modal ── */}
      <Modal visible={imageGalleryOpen} transparent animationType="slide" onRequestClose={() => { setImageGalleryOpen(false); setSelectedUrls(new Set()) }}>
        <Pressable style={s.imageSheetBackdrop} onPress={() => { setImageGalleryOpen(false); setSelectedUrls(new Set()) }}>
          <Pressable style={s.gallerySheet} onPress={e => e.stopPropagation()}>
            <View style={s.galleryHeader}>
              <Text style={s.galleryTitle}>{imageLabels.title}</Text>
              <Text style={s.galleryCount}>{pageImages.length}</Text>
            </View>
            <View style={s.galleryActions}>
              {selectedUrls.size > 0 ? (
                <Pressable
                  style={[s.imageActionBtn, savingSelected && { opacity: 0.45 }]}
                  disabled={savingSelected}
                  onPress={handleSaveSelected}
                >
                  {savingSelected
                    ? <ActivityIndicator color="#fff" size="small" />
                    : <Text style={s.imageActionText}>{imageLabels.save} ({selectedUrls.size})</Text>
                  }
                </Pressable>
              ) : (
                <Pressable
                  style={[s.imageActionBtn, (pageImages.length === 0 || savingAll) && { opacity: 0.45 }]}
                  disabled={pageImages.length === 0 || savingAll}
                  onPress={handleSaveAllImages}
                >
                  {savingAll
                    ? <ActivityIndicator color="#fff" size="small" />
                    : <Text style={s.imageActionText}>{imageLabels.save} all</Text>
                  }
                </Pressable>
              )}
              <Pressable
                style={[s.imageActionBtn, s.imageActionBtnAlt, pageImages.length === 0 && { opacity: 0.45 }]}
                disabled={pageImages.length === 0}
                onPress={handleShareAllImages}
              >
                <Text style={[s.imageActionText, s.imageActionTextAlt]}>{imageLabels.share} all</Text>
              </Pressable>
              <Pressable style={[s.imageActionBtn, s.imageActionBtnAlt]} onPress={() => { setImageGalleryOpen(false); setSelectedUrls(new Set()) }}>
                <Text style={[s.imageActionText, s.imageActionTextAlt]}>{imageLabels.close}</Text>
              </Pressable>
            </View>
            {pageImages.length === 0 ? (
              <Text style={s.galleryEmpty}>No images found on this page.</Text>
            ) : (
              <ScrollView>
                <View style={s.galleryGrid}>
                  {pageImages.map((img, index) => {
                    const isSelected = selectedUrls.has(img.url)
                    return (
                      <Pressable
                        key={`${img.url}:${index}`}
                        style={[s.galleryThumbBtn, isSelected && s.galleryThumbSelected]}
                        onPress={() => {
                          setSelectedUrls(prev => {
                            const next = new Set(prev)
                            if (next.has(img.url)) next.delete(img.url)
                            else next.add(img.url)
                            return next
                          })
                        }}
                        onLongPress={() => setSelectedImage(img)}
                      >
                        <Image source={{ uri: img.url }} style={s.galleryThumb} resizeMode="cover" />
                        {isSelected && (
                          <View style={s.galleryCheckOverlay}>
                            <Ionicons name="checkmark-circle" size={24} color="#fff" />
                          </View>
                        )}
                      </Pressable>
                    )
                  })}
                </View>
              </ScrollView>
            )}
          </Pressable>
        </Pressable>
      </Modal>

      {/* ── Single image action modal ── */}
      <Modal visible={!!selectedImage} transparent animationType="slide" onRequestClose={() => setSelectedImage(null)}>
        <Pressable style={s.imageSheetBackdrop} onPress={() => setSelectedImage(null)}>
          <Pressable style={s.imageSheet} onPress={e => e.stopPropagation()}>
            {selectedImage && (
              <>
                <Image source={{ uri: selectedImage.url }} style={s.imagePreview} resizeMode="contain" />
                <Text style={s.imageTitle} numberOfLines={2}>{selectedImage.alt || imageLabels.title}</Text>
                <Text style={s.imageUrl} numberOfLines={1} selectable>{selectedImage.url}</Text>
                <View style={s.imageActions}>
                  <Pressable style={s.imageActionBtn} onPress={() => handleSaveImage(selectedImage!.url)}>
                    <Text style={s.imageActionText}>{imageLabels.save}</Text>
                  </Pressable>
                  <Pressable style={[s.imageActionBtn, s.imageActionBtnAlt]} onPress={handleShareImage}>
                    <Text style={[s.imageActionText, s.imageActionTextAlt]}>{imageLabels.share}</Text>
                  </Pressable>
                  <Pressable style={[s.imageActionBtn, s.imageActionBtnAlt]} onPress={handleOpenImage}>
                    <Text style={[s.imageActionText, s.imageActionTextAlt]}>{imageLabels.open}</Text>
                  </Pressable>
                </View>
                <Pressable style={[s.imageActionBtn, s.imageActionBtnAlt]} onPress={() => setSelectedImage(null)}>
                  <Text style={[s.imageActionText, s.imageActionTextAlt]}>{imageLabels.close}</Text>
                </Pressable>
              </>
            )}
          </Pressable>
        </Pressable>
      </Modal>

      {/* ── Bottom toolbar: all action buttons ── */}
      <View style={s.bottomBar}>
        <Pressable onPress={handleGoBackInPage} disabled={!canGoBack} style={[s.navBtn, !canGoBack && s.navBtnOff]} hitSlop={8}>
          <Ionicons name="chevron-back" size={19} color={theme.text} />
        </Pressable>
        <Pressable onPress={handleGoForwardInPage} disabled={!canGoForward} style={[s.navBtn, !canGoForward && s.navBtnOff]} hitSlop={8}>
          <Ionicons name="chevron-forward" size={19} color={theme.text} />
        </Pressable>
        <View style={s.barDivider} />
        <Pressable onPress={collectPageImages} style={[s.barBtn, collectingImages && s.barBtnActive]} hitSlop={8}>
          {collectingImages
            ? <ActivityIndicator size="small" color={theme.primary} />
            : <Ionicons name="images-outline" size={19} color={collectingImages ? theme.primary : theme.text} />
          }
        </Pressable>
        <Pressable
          onPress={handleReaderMode}
          disabled={loadingReaderMode}
          style={[s.barBtn, readerModeContent && s.barBtnActive, loadingReaderMode && s.barBtnDisabled]}
          hitSlop={8}
        >
          {loadingReaderMode
            ? <ActivityIndicator size="small" color={theme.primary} />
            : <Ionicons name="book-outline" size={19} color={readerModeContent ? theme.primary : theme.text} />
          }
        </Pressable>
        <Pressable
          onPress={() => { if (!localContent) setIsTranslated(v => !v) }}
          disabled={!!localContent}
          style={[s.barBtn, isTranslated && s.barBtnActive, localContent && s.barBtnDisabled]}
          hitSlop={8}
        >
          <Ionicons name="language" size={19} color={isTranslated ? theme.primary : theme.text} />
        </Pressable>
        <View style={s.barDivider} />
        <Pressable onPress={handleRefresh} style={s.barBtn} hitSlop={8}>
          <Ionicons name="refresh" size={19} color={theme.text} />
        </Pressable>
        <Pressable onPress={() => Linking.openURL(readerUrl)} style={s.barBtn} hitSlop={8}>
          <Ionicons name="open-outline" size={19} color={theme.text} />
        </Pressable>
      </View>
    </View>
  )
}
