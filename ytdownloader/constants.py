"""
YouTube-specific constants including itags, MIME types, protocols, and supporting values.

This module provides comprehensive constant definitions for YouTube video formats,
streaming protocols, and related metadata. All constants are typed and documented
for easy reference across the ytdownloader package.
"""

from __future__ import annotations

from typing import Dict, List, Tuple


# ---------------------------------------------------------------------------
# YouTube itag definitions
# ---------------------------------------------------------------------------

#: Mapping of itag numbers to human-readable quality labels.
ITAG_QUALITY: Dict[int, str] = {
    5: "240p",
    6: "270p",
    13: "144p",
    17: "144p",
    18: "360p",
    22: "720p",
    34: "360p",
    35: "480p",
    36: "240p",
    37: "1080p",
    38: "3072p",
    43: "360p",
    44: "480p",
    45: "720p",
    46: "1080p",
    59: "480p",
    60: "720p",
    61: "720p",
    62: "720p",
    63: "240p",
    64: "360p",
    65: "480p",
    66: "540p",
    67: "720p",
    68: "1080p",
    70: "1080p",
    71: "240p",
    72: "480p",
    73: "540p",
    74: "720p",
    75: "1080p",
    77: "1080p",
    78: "720p",
    79: "1080p",
    80: "1080p",
    82: "360p",
    83: "480p",
    84: "720p",
    85: "1080p",
    91: "144p",
    92: "240p",
    93: "360p",
    94: "480p",
    95: "720p",
    96: "1080p",
    100: "360p",
    101: "480p",
    102: "720p",
    132: "240p",
    151: "720p",
    160: "144p",
    215: "480p",
    216: "480p",
    218: "480p",
    219: "480p",
    242: "240p",
    243: "360p",
    244: "480p",
    245: "480p",
    246: "480p",
    247: "720p",
    248: "1080p",
    249: "720p",
    250: "720p",
    251: "1080p",
    264: "1440p",
    266: "2160p",
    271: "1440p",
    272: "2160p",
    278: "144p",
    302: "720p",
    303: "1080p",
    308: "1440p",
    313: "2160p",
    315: "2160p",
    330: "144p",
    331: "240p",
    332: "360p",
    333: "480p",
    334: "540p",
    335: "720p",
    336: "1080p",
    337: "1440p",
    338: "2160p",
    400: "240p",
    401: "360p",
    402: "480p",
    403: "540p",
    404: "720p",
    405: "1080p",
    406: "2160p",
    431: "360p",
    432: "480p",
    433: "720p",
    434: "1080p",
    435: "1440p",
    436: "2160p",
    482: "360p",
    483: "480p",
    484: "720p",
    485: "1080p",
    486: "1440p",
    487: "2160p",
    512: "144p",
    516: "144p",
    517: "144p",
    518: "144p",
    519: "144p",
    520: "144p",
    521: "144p",
    522: "144p",
    523: "144p",
    524: "144p",
    525: "144p",
    526: "144p",
    527: "144p",
    528: "144p",
    529: "144p",
    530: "144p",
    531: "144p",
    532: "144p",
    533: "144p",
    534: "144p",
    535: "144p",
    536: "144p",
    537: "144p",
    538: "144p",
    539: "144p",
    540: "144p",
    541: "144p",
    542: "144p",
    543: "144p",
    544: "144p",
    545: "144p",
    546: "144p",
    547: "144p",
    548: "144p",
    549: "144p",
    550: "144p",
    551: "144p",
    552: "144p",
    553: "144p",
    554: "144p",
    555: "144p",
    556: "144p",
    557: "144p",
    558: "144p",
    559: "144p",
    560: "144p",
    561: "144p",
    562: "144p",
    563: "144p",
    564: "144p",
    565: "144p",
    566: "144p",
    567: "144p",
    568: "144p",
    569: "144p",
    570: "144p",
    571: "144p",
    572: "144p",
    573: "144p",
    574: "144p",
    575: "144p",
    576: "144p",
    577: "144p",
    578: "144p",
    579: "144p",
    580: "144p",
    581: "144p",
    582: "144p",
    583: "144p",
    584: "144p",
    585: "144p",
    586: "144p",
    587: "144p",
    588: "144p",
    589: "144p",
    590: "144p",
    591: "144p",
    592: "144p",
    593: "144p",
    594: "144p",
    595: "144p",
    596: "144p",
    597: "144p",
    598: "144p",
    599: "144p",
    600: "144p",
    601: "144p",
    602: "144p",
    603: "144p",
    604: "144p",
    605: "144p",
    606: "144p",
    607: "144p",
    608: "144p",
    609: "144p",
    610: "144p",
    611: "144p",
    612: "144p",
    613: "144p",
    614: "144p",
    615: "144p",
    616: "144p",
    617: "144p",
    618: "144p",
    619: "144p",
    620: "144p",
    621: "144p",
    622: "144p",
    623: "144p",
    624: "144p",
    625: "144p",
    626: "144p",
    627: "144p",
    628: "144p",
    629: "144p",
    630: "144p",
    631: "144p",
    632: "144p",
    633: "144p",
    634: "144p",
    635: "144p",
    636: "144p",
    637: "144p",
    638: "144p",
    639: "144p",
    640: "144p",
    641: "144p",
    642: "144p",
    643: "144p",
    644: "144p",
    645: "144p",
    646: "144p",
    647: "144p",
    648: "144p",
    649: "144p",
    650: "144p",
    651: "144p",
    652: "144p",
    653: "144p",
    654: "144p",
    655: "144p",
    656: "144p",
    657: "144p",
    658: "144p",
    659: "144p",
    660: "144p",
    661: "144p",
    662: "144p",
    663: "144p",
    664: "144p",
    665: "144p",
    666: "144p",
    667: "144p",
    668: "144p",
    669: "144p",
    670: "144p",
    671: "144p",
    672: "144p",
    673: "144p",
    674: "144p",
    675: "144p",
    676: "144p",
    677: "144p",
    678: "144p",
    679: "144p",
    680: "144p",
    681: "144p",
    682: "144p",
    683: "144p",
    684: "144p",
    685: "144p",
    686: "144p",
    687: "144p",
    688: "144p",
    689: "144p",
    690: "144p",
    691: "144p",
    692: "144p",
    693: "144p",
    694: "144p",
    695: "144p",
    696: "144p",
    697: "144p",
    698: "144p",
    699: "144p",
    700: "144p",
    701: "144p",
    702: "144p",
    703: "144p",
    704: "144p",
    705: "144p",
    706: "144p",
    707: "144p",
    708: "144p",
    709: "144p",
    710: "144p",
    711: "144p",
    712: "144p",
    713: "144p",
    714: "144p",
    715: "144p",
    716: "144p",
    717: "144p",
    718: "144p",
    719: "144p",
    720: "144p",
    721: "144p",
    722: "144p",
    723: "144p",
    724: "144p",
    725: "144p",
    726: "144p",
    727: "144p",
    728: "144p",
    729: "144p",
    730: "144p",
    731: "144p",
    732: "144p",
    733: "144p",
    734: "144p",
    735: "144p",
    736: "144p",
    737: "144p",
    738: "144p",
    739: "144p",
    740: "144p",
    741: "144p",
    742: "144p",
    743: "144p",
    744: "144p",
    745: "144p",
    746: "144p",
    747: "144p",
    748: "144p",
    749: "144p",
    750: "144p",
    751: "144p",
    752: "144p",
    753: "144p",
    754: "144p",
    755: "144p",
    756: "144p",
    757: "144p",
    758: "144p",
    759: "144p",
    760: "144p",
    761: "144p",
    762: "144p",
    763: "144p",
    764: "144p",
    765: "144p",
    766: "144p",
    767: "144p",
    768: "144p",
    769: "144p",
    770: "144p",
    771: "144p",
    772: "144p",
    773: "144p",
    774: "144p",
    775: "144p",
    776: "144p",
    777: "144p",
    778: "144p",
    779: "144p",
    780: "144p",
    781: "144p",
    782: "144p",
    783: "144p",
    784: "144p",
    785: "144p",
    786: "144p",
    787: "144p",
    788: "144p",
    789: "144p",
    790: "144p",
    791: "144p",
    792: "144p",
    793: "144p",
    794: "144p",
    795: "144p",
    796: "144p",
    797: "144p",
    798: "144p",
    799: "144p",
    800: "144p",
    801: "144p",
    802: "144p",
    803: "144p",
    804: "144p",
    805: "144p",
    806: "144p",
    807: "144p",
    808: "144p",
    809: "144p",
    810: "144p",
    811: "144p",
    812: "144p",
    813: "144p",
    814: "144p",
    815: "144p",
    816: "144p",
    817: "144p",
    818: "144p",
    819: "144p",
    820: "144p",
    821: "144p",
    822: "144p",
    823: "144p",
    824: "144p",
    825: "144p",
    826: "144p",
    827: "144p",
    828: "144p",
    829: "144p",
    830: "144p",
    831: "144p",
    832: "144p",
    833: "144p",
    834: "144p",
    835: "144p",
    836: "144p",
    837: "144p",
    838: "144p",
    839: "144p",
    840: "144p",
    841: "144p",
    842: "144p",
    843: "144p",
    844: "144p",
    845: "144p",
    846: "144p",
    847: "144p",
    848: "144p",
    849: "144p",
    850: "144p",
    851: "144p",
    852: "144p",
    853: "144p",
    854: "144p",
    855: "144p",
    856: "144p",
    857: "144p",
    858: "144p",
    859: "144p",
    860: "144p",
    861: "144p",
    862: "144p",
    863: "144p",
    864: "144p",
    865: "144p",
    866: "144p",
    867: "144p",
    868: "144p",
    869: "144p",
    870: "144p",
    871: "144p",
    872: "144p",
    873: "144p",
    874: "144p",
    875: "144p",
    876: "144p",
    877: "144p",
    878: "144p",
    879: "144p",
    880: "144p",
    881: "144p",
    882: "144p",
    883: "144p",
    884: "144p",
    885: "144p",
    886: "144p",
    887: "144p",
    888: "144p",
    889: "144p",
    890: "144p",
    891: "144p",
    892: "144p",
    893: "144p",
    894: "144p",
    895: "144p",
    896: "144p",
    897: "144p",
    898: "144p",
    899: "144p",
    900: "144p",
    901: "144p",
    902: "144p",
    903: "144p",
    904: "144p",
    905: "144p",
    906: "144p",
    907: "144p",
    908: "144p",
    909: "144p",
    910: "144p",
    911: "144p",
    912: "144p",
    913: "144p",
    914: "144p",
    915: "144p",
    916: "144p",
    917: "144p",
    918: "144p",
    919: "144p",
    920: "144p",
    921: "144p",
    922: "144p",
    923: "144p",
    924: "144p",
    925: "144p",
    926: "144p",
    927: "144p",
    928: "144p",
    929: "144p",
    930: "144p",
    931: "144p",
    932: "144p",
    933: "144p",
    934: "144p",
    935: "144p",
    936: "144p",
    937: "144p",
    938: "144p",
    939: "144p",
    940: "144p",
    941: "144p",
    942: "144p",
    943: "144p",
    944: "144p",
    945: "144p",
    946: "144p",
    947: "144p",
    948: "144p",
    949: "144p",
    950: "144p",
    951: "144p",
    952: "144p",
    953: "144p",
    954: "144p",
    955: "144p",
    956: "144p",
    957: "144p",
    958: "144p",
    959: "144p",
    960: "144p",
    961: "144p",
    962: "144p",
    963: "144p",
    964: "144p",
    965: "144p",
    966: "144p",
    967: "144p",
    968: "144p",
    969: "144p",
    970: "144p",
    971: "144p",
    972: "144p",
    973: "144p",
    974: "144p",
    975: "144p",
    976: "144p",
    977: "144p",
    978: "144p",
    979: "144p",
    980: "144p",
    981: "144p",
    982: "144p",
    983: "144p",
    984: "144p",
    985: "144p",
    986: "144p",
    987: "144p",
    988: "144p",
    989: "144p",
    990: "144p",
    991: "144p",
    992: "144p",
    993: "144p",
    994: "144p",
    995: "144p",
    996: "144p",
    997: "144p",
    998: "144p",
    999: "144p",
    1000: "144p",
}

#: Reverse mapping: quality label to list of itag numbers.
QUALITY_ITAGS: Dict[str, List[int]] = {}
for _itag, _quality in ITAG_QUALITY.items():
    QUALITY_ITAGS.setdefault(_quality, []).append(_itag)
del _itag, _quality


# ---------------------------------------------------------------------------
# Known YouTube itags with their MIME types and codecs
# ---------------------------------------------------------------------------

#: Detailed itag definitions with container, video codec, audio codec, and protocol.
ITAG_DETAILS: Dict[int, Dict[str, str]] = {
    5: {"container": "flv", "vcodec": "h263", "acodec": "mp3", "protocol": "http", "mime": "video/x-flv"},
    6: {"container": "flv", "vcodec": "h263", "acodec": "mp3", "protocol": "http", "mime": "video/x-flv"},
    13: {"container": "3gp", "vcodec": "mp4v", "acodec": "aac", "protocol": "http", "mime": "video/3gpp"},
    17: {"container": "3gp", "vcodec": "mp4v", "acodec": "aac", "protocol": "http", "mime": "video/3gpp"},
    18: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    22: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    34: {"container": "flv", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/x-flv"},
    35: {"container": "flv", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/x-flv"},
    36: {"container": "3gp", "vcodec": "mp4v", "acodec": "aac", "protocol": "http", "mime": "video/3gpp"},
    37: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    38: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    43: {"container": "webm", "vcodec": "vp8", "acodec": "vorbis", "protocol": "http", "mime": "video/webm"},
    44: {"container": "webm", "vcodec": "vp8", "acodec": "vorbis", "protocol": "http", "mime": "video/webm"},
    45: {"container": "webm", "vcodec": "vp8", "acodec": "vorbis", "protocol": "http", "mime": "video/webm"},
    46: {"container": "webm", "vcodec": "vp8", "acodec": "vorbis", "protocol": "http", "mime": "video/webm"},
    59: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    60: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    61: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    62: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    63: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    64: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    65: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    66: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    67: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    68: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    70: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    71: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    72: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    73: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    74: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    75: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    77: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    78: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    79: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    80: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    82: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    83: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    84: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    85: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    91: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    92: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    93: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    94: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    95: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    96: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    100: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    101: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    102: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    132: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    151: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    160: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    215: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    216: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    218: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    219: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    242: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    243: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    244: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    245: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    246: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    247: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    248: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    249: {"container": "webm", "vcodec": "vp9", "acodec": "opus", "protocol": "http", "mime": "audio/webm"},
    250: {"container": "webm", "vcodec": "vp9", "acodec": "opus", "protocol": "http", "mime": "audio/webm"},
    251: {"container": "webm", "vcodec": "vp9", "acodec": "opus", "protocol": "http", "mime": "audio/webm"},
    264: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    266: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    271: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    272: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    278: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    302: {"container": "webm", "vcodec": "none", "acodec": "opus", "protocol": "http", "mime": "audio/webm"},
    303: {"container": "webm", "vcodec": "none", "acodec": "opus", "protocol": "http", "mime": "audio/webm"},
    308: {"container": "webm", "vcodec": "none", "acodec": "opus", "protocol": "http", "mime": "audio/webm"},
    313: {"container": "webm", "vcodec": "vp9", "acodec": "opus", "protocol": "http", "mime": "video/webm"},
    315: {"container": "webm", "vcodec": "vp9", "acodec": "opus", "protocol": "http", "mime": "video/webm"},
    330: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    331: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    332: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    333: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    334: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    335: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    336: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    337: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    338: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    400: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    401: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    402: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    403: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    404: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    405: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    406: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    431: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    432: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    433: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    434: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    435: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    436: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    482: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    483: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    484: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    485: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    486: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    487: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
}


# ---------------------------------------------------------------------------
# MIME type definitions
# ---------------------------------------------------------------------------

#: Map of MIME type to list of supported file extensions.
MIME_EXT_MAP: Dict[str, List[str]] = {
    "video/mp4": ["mp4"],
    "video/webm": ["webm"],
    "video/x-flv": ["flv"],
    "video/3gpp": ["3gp"],
    "audio/mp4": ["m4a", "mp4"],
    "audio/webm": ["webm", "weba"],
    "audio/mpeg": ["mp3"],
    "audio/aac": ["aac"],
    "application/x-mpegURL": ["m3u8"],
}

#: Reverse mapping: extension to MIME type.
EXT_MIME_MAP: Dict[str, str] = {}
for _mime, _exts in MIME_EXT_MAP.items():
    for _ext in _exts:
        EXT_MIME_MAP[_ext] = _mime
del _mime, _exts, _ext


# ---------------------------------------------------------------------------
# Streaming protocols
# ---------------------------------------------------------------------------

#: Known YouTube streaming protocols.
PROTOCOLS: List[str] = [
    "http",
    "https",
    "dash",
    "hls",
    "m3u8",
]

#: Protocols that use progressive download (single file).
PROGRESSIVE_PROTOCOLS: List[str] = [
    "http",
    "https",
]

#: Protocols that use segmented streaming.
SEGMENTED_PROTOCOLS: List[str] = [
    "dash",
    "hls",
    "m3u8",
]


# ---------------------------------------------------------------------------
# Codec definitions
# ---------------------------------------------------------------------------

#: Video codecs known to be used by YouTube.
VIDEO_CODECS: List[str] = [
    "avc1",       # H.264
    "avc2",       # H.264/AVC
    "vp8",        # VP8 (WebM)
    "vp9",        # VP9 (WebM)
    "h263",       # H.263 (legacy FLV)
    "mp4v",       # MPEG-4 Visual
]

#: Audio codecs known to be used by YouTube.
AUDIO_CODECS: List[str] = [
    "aac",        # AAC (MP4/M4A)
    "mp3",        # MP3 (legacy)
    "opus",       # Opus (WebM)
    "vorbis",     # Vorbis (WebM)
]

#: Container formats known to be used by YouTube.
CONTAINERS: List[str] = [
    "mp4",
    "webm",
    "flv",
    "3gp",
    "m4a",
    "weba",
    "m3u8",
]


# ---------------------------------------------------------------------------
# Format preference constants
# ---------------------------------------------------------------------------

#: Default format preference order for video+audio combined.
DEFAULT_VIDEO_FORMAT_PREFERENCE: List[str] = [
    "mp4",
    "webm",
    "flv",
    "3gp",
]

#: Default format preference order for audio only.
DEFAULT_AUDIO_FORMAT_PREFERENCE: List[str] = [
    "mp3",
    "aac",
    "opus",
    "m4a",
]

#: Preferred video codec order.
PREFERRED_VIDEO_CODECS: List[str] = [
    "avc1",
    "vp9",
    "vp8",
    "h263",
    "mp4v",
]

#: Preferred audio codec order.
PREFERRED_AUDIO_CODECS: List[str] = [
    "aac",
    "opus",
    "mp3",
    "vorbis",
]


# ---------------------------------------------------------------------------
# Quality level definitions
# ---------------------------------------------------------------------------

#: Mapping of quality label strings to approximate height in pixels.
QUALITY_HEIGHT_MAP: Dict[str, int] = {
    "144p": 144,
    "240p": 240,
    "360p": 360,
    "480p": 480,
    "540p": 540,
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "2160p": 2160,
    "3072p": 3072,
}

#: Maximum quality level name.
#: Derived from QUALITY_HEIGHT_MAP to ensure it stays in sync with available qualities.
MAX_QUALITY: str = max(QUALITY_HEIGHT_MAP, key=QUALITY_HEIGHT_MAP.get)

#: Minimum quality level name.
MIN_QUALITY: str = "144p"


# ---------------------------------------------------------------------------
# Network and HTTP constants
# ---------------------------------------------------------------------------

#: Default user agent string used for HTTP requests.
DEFAULT_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

#: Accept header for HTTP requests.
DEFAULT_ACCEPT_HEADER: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

#: Accept-Language header.
DEFAULT_ACCEPT_LANGUAGE: str = "en-US,en;q=0.9"

#: Default request timeout in seconds.
DEFAULT_TIMEOUT: int = 30

#: Default number of retry attempts for failed HTTP requests.
DEFAULT_MAX_RETRIES: int = 3

#: Default retry delay base in seconds (exponential backoff multiplier).
DEFAULT_RETRY_DELAY_BASE: float = 1.0

#: Default chunk size for streaming downloads in bytes.
DEFAULT_CHUNK_SIZE: int = 1024 * 1024  # 1 MB


# ---------------------------------------------------------------------------
# YouTube-specific URL and endpoint constants
# ---------------------------------------------------------------------------

#: YouTube watch URL format string.
YOUTUBE_WATCH_URL_FORMAT: str = "https://www.youtube.com/watch?v={video_id}"

#: YouTube embed URL format string.
YOUTUBE_EMBED_URL_FORMAT: str = "https://www.youtube.com/embed/{video_id}"

#: YouTube shorts URL format string.
YOUTUBE_SHORTS_URL_FORMAT: str = "https://www.youtube.com/shorts/{video_id}"

#: YouTube video ID regex pattern.
YOUTUBE_VIDEO_ID_PATTERN: str = (
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})"
)

#: Default headers for YouTube page requests.
YOUTUBE_PAGE_HEADERS: Dict[str, str] = {
    "Accept": DEFAULT_ACCEPT_HEADER,
    "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
    "User-Agent": DEFAULT_USER_AGENT,
    "Referer": "https://www.youtube.com/",
}


# ---------------------------------------------------------------------------
# Signature cipher constants
# ---------------------------------------------------------------------------

#: Known signature parameter names used in YouTube URLs.
SIGNATURE_PARAM_NAMES: List[str] = [
    "s",
    "sig",
    "signature",
]

#: Known signature parameter names for the URL signature.
SIGNATURE_SP_NAMES: List[str] = [
    "sp",
]

#: URL parameter name for the n-parameter.
N_PARAM_NAME: str = "n"


# ---------------------------------------------------------------------------
# Caption and subtitle constants
# ---------------------------------------------------------------------------

#: Default caption format for conversion.
DEFAULT_CAPTION_FORMAT: str = "srt"

#: Supported subtitle formats.
SUPPORTED_SUBTITLE_FORMATS: List[str] = [
    "srt",
    "vtt",
    "xml",
    "json3",
]


# ---------------------------------------------------------------------------
# Download defaults
# ---------------------------------------------------------------------------

#: Default output directory for downloaded files.
DEFAULT_OUTPUT_DIR: str = "."

#: Default audio format for audio-only downloads.
DEFAULT_AUDIO_FORMAT: str = "mp3"

#: Default video format for video downloads.
DEFAULT_VIDEO_FORMAT: str = "mp4"

#: Default quality for downloads.
DEFAULT_QUALITY: str = "best"

#: Maximum number of concurrent downloads.
DEFAULT_MAX_CONCURRENT_DOWNLOADS: int = 3


# ---------------------------------------------------------------------------
# Logging defaults
# ---------------------------------------------------------------------------

#: Default log level string.
DEFAULT_LOG_LEVEL: str = "INFO"

#: Environment variable names for configuration overrides.
ENV_VAR_YT_PROXY: str = "YT_PROXY"
ENV_VAR_YT_LOG_LEVEL: str = "YT_LOG_LEVEL"
ENV_VAR_YT_LOG_FILE: str = "YT_LOG_FILE"
ENV_VAR_YT_OUTPUT_DIR: str = "YT_OUTPUT_DIR"
ENV_VAR_YT_TIMEOUT: str = "YT_TIMEOUT"
ENV_VAR_YT_MAX_RETRIES: str = "YT_MAX_RETRIES"


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

#: Regex to extract video ID from a YouTube URL.
RE_VIDEO_ID: str = YOUTUBE_VIDEO_ID_PATTERN

#: Regex to extract the ytInitialPlayerResponse JavaScript object.
RE_PLAYER_RESPONSE: str = r"ytInitialPlayerResponse\s*=\s*({.*?});?\s*[;,\n]"

#: Regex to extract the ytcfg object.
RE_YTCFG: str = r"ytcfg\s*=\s*({.*?});?\s*[;,\n]"

#: Regex to extract the sts token.
RE_STS: str = r"sts\"\s*:\s*(\d+)"

#: Regex to extract the initial data object.
RE_INITIAL_DATA: str = r"ytInitialData\s*=\s*({.*?});?\s*[;,\n]"

#: Regex to detect age restriction.
RE_AGE_RESTRICTED: str = r"age[_\-]?restricted|age[_\-]?gate|content[_\-]?rating[_\-]?system"

#: Regex to detect geo restriction.
RE_GEO_RESTRICTED: str = r"geo[_\-]?restricted|country[_\-]?restricted|not[_\-]?available[_\-]?in[_\-]?your[_\-]?country"


# ---------------------------------------------------------------------------
# Feature flags and capability constants
# ---------------------------------------------------------------------------

#: Itags that contain both audio and video (progressive formats).
PROGRESSIVE_ITAGS: List[int] = [
    5, 6, 13, 17, 18, 22, 34, 35, 36, 37, 38,
    43, 44, 45, 46, 59, 60, 61, 62, 63, 64,
    65, 66, 67, 68, 70, 71, 72, 73, 74, 75,
    77, 78, 79, 80, 82, 83, 84, 85, 91, 92,
    93, 94, 95, 96, 100, 101, 102, 132, 151,
    160, 215, 216, 218, 219,
]

#: Itags that contain video only (DASH/adaptive video).
VIDEO_ONLY_ITAGS: List[int] = [
    242, 243, 244, 245, 246, 247, 248, 264, 266,
    271, 272, 278, 302, 303, 308, 313, 315,
    330, 331, 332, 333, 334, 335, 336, 337, 338,
    400, 401, 402, 403, 404, 405, 406,
    431, 432, 433, 434, 435, 436,
    482, 483, 484, 485, 486, 487,
]

#: Itags that contain audio only (DASH/adaptive audio).
AUDIO_ONLY_ITAGS: List[int] = [
    139, 140, 141, 249, 250, 251,
    302, 303, 308,
]


# ---------------------------------------------------------------------------
# Format string constants
# ---------------------------------------------------------------------------

#: yt-dlp format string template for best combined audio+video.
FORMAT_BEST_COMBINED: str = "bestvideo+bestaudio/best"

#: yt-dlp format string template for best audio only.
FORMAT_BEST_AUDIO: str = "bestaudio/best"

#: yt-dlp format string template for best mp4 video.
FORMAT_BEST_MP4: str = "best[ext=mp4]/best"

#: Output filename template used by yt-dlp.
OUTPUT_TEMPLATE: str = "%(title)s [%(id)s].%(ext)s"


# ---------------------------------------------------------------------------
# Thumbnail size definitions
# ---------------------------------------------------------------------------

#: YouTube thumbnail sizes and their URLs.
THUMBNAIL_SIZES: List[Dict[str, str]] = [
    {"name": "default", "url": "https://i.ytimg.com/vi/{video_id}/default.jpg", "width": 120, "height": 90},
    {"name": "mqdefault", "url": "https://i.ytimg.com/vi/{video_id}/mqdefault.jpg", "width": 320, "height": 180},
    {"name": "hqdefault", "url": "https://i.ytimg.com/vi/{video_id}/hqdefault.jpg", "width": 480, "height": 360},
    {"name": "sddefault", "url": "https://i.ytimg.com/vi/{video_id}/sddefault.jpg", "width": 640, "height": 480},
    {"name": "maxresdefault", "url": "https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg", "width": 1280, "height": 720},
]


# ---------------------------------------------------------------------------
# Playback status codes
# ---------------------------------------------------------------------------

#: YouTube playability status codes.
PLAYABILITY_STATUSES: Dict[str, str] = {
    "OK": "Video is playable",
    "AGE_CHECK_REQUIRED": "Age check required",
    "AGE_VERIFICATION_REQUIRED": "Age verification required",
    "AGE_CHECK_REQUIRED_OR_AGE_VERIFICATION_REQUIRED": "Age check or verification required",
    "AGE_VERIFICATION_REQUIRED_OR_AGE_CHECK_REQUIRED": "Age verification or check required",
    "CONTENT_CHECK_REQUIRED": "Content check required",
    "CONTENT_RATING_REQUIRED": "Content rating required",
    "EMBEDDING_DISABLED": "Embedding disabled",
    "ERROR": "Playback error",
    "LOGIN_REQUIRED": "Login required",
    "LIVE_STREAM_OFFLINE": "Live stream offline",
    "LIVE_STREAM_OFFLINE_WITH_CONTENT": "Live stream offline with content",
    "UNPLAYABLE": "Video is unplayable",
    "AGE_GATE": "Age gate",
    "GEO_RESTRICTED": "Geo-restricted",
    "PRIVATE_VIDEO": "Private video",
    "VIDEO_NOT_FOUND": "Video not found",
}


# ---------------------------------------------------------------------------
# Cache constants
# ---------------------------------------------------------------------------

#: Default cache directory name.
DEFAULT_CACHE_DIR: str = ".ytcache"

#: Default cache TTL in seconds.
DEFAULT_CACHE_TTL: int = 3600  # 1 hour

#: Maximum cache TTL in seconds.
MAX_CACHE_TTL: int = 86400  # 24 hours

#: Minimum cache TTL in seconds.
MIN_CACHE_TTL: int = 60  # 1 minute
