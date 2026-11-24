// Creative Detail window → every field it shows (Mika 24.11 18:25: "בסושיאל פיטה יש הרבה נתונים על המודעות - תוסיף!!")
(function(){var md=[].slice.call(document.querySelectorAll(".ant-modal")).filter(function(m){return m.offsetParent!==null||getComputedStyle(m).display!=="none"});if(!md.length)return JSON.stringify({err:"nomodal"});md=md[md.length-1];var t=md.innerText;
var g=function(a,b){var i=t.indexOf(a);if(i<0)return null;var s=t.slice(i+a.length);var j=b?s.indexOf(b):-1;return (j>=0?s.slice(0,j):s.slice(0,120)).trim()};
var v=md.querySelector("video");
var reg=g("Region\n","\nSize")||"";var rc=(reg.match(/\((\d+)\)\s*$/)||[])[1];
var cats=(t.match(/^[A-Z][A-Za-z&]+: .+$/mg)||[]).filter(function(x){return !/^(Impression|Duration)/.test(x)});
return JSON.stringify({video:v?(v.currentSrc||v.src):null,poster:v?v.poster:null,
 drama:g("Related Drama\n","\n"),impr:g("Impression\n","\n"),dur:g("Duration\n","\n"),acct:g("Ad Account\n","\n"),
 desc:g("Watch More\nDownload\n","\nDisclaimer"),versions:(t.match(/There are (\d+) versions? of this ad/)||[])[1]||null,
 network:g("Network\n","\n"),language:g("Language\n","\n"),regions:rc?+rc:(reg?reg.split("/").length:null),
 size:(g("Size\n","\nAd Account")||"").replace(/\n/g," "),score_week:g("Score (This Week)\n","\n"),score_total:g("Total Score\n","\n"),
 first_seen:(g("First Seen\n","\n")||"").replace(/^\(UTC[^)]*\)\s*/,""),categories:cats.join(" · "),
 drama_creatives:(t.match(/Creatives\s*([\d.]+[KMB]?)\s*\n/)||[])[1]||null,drama_ad_days:(t.match(/Ads Duration\s*(\d+)/)||[])[1]||null})})()
