    1 ifpeek(53247)<>1thenpoke808,227
   10 ifzgoto20
   11 z=1:load"mf-zeichen",8,1
   20 sys49972,40960,43007,53248,52
   21 poke53265,peek(53265)and239:poke56576,148:poke648,204:poke53272,52
   30 gosub100:gosub170:gosub200:goto1000
  100 dim lk$(12),gn$(4,10),gw(4,10),ge$(4,10),wl(4,4),sg(30),lm(12),kp(2,10)
  101 dims9(4),tk(5),tr(5),wp(8),ts(8),tg(8),ws(8),po(4),ra(4),nr(4),ka(4),gz(10)
  103 dim x5%(4),x6%(4)
  110 ra=53280:fr=55296:br=52224:bl=49387:rc=49972:lc=49592:i1=49470:ad=40960
  111 ie=49560:ua=167:cs=58640:si=54272:cr=49152:po=53240:v=53248:lh=49752
  112 so=50176
  115 deffnm(ln)=50-50*(ln=3orln=4)-100*(ln=1)
  116 deffnwe(x)=(x=4)or(x=14)or(x=27)or(x=30)
  117 deffnlb(x)=xand255:deffnhb(x)=int(x/256):deffnr(x)=int(rnd(1)*8)+8
  120 fori=1to12:readlk$(i):next
  121 fori=0to8:readwa$(i),wp(i),ts(i),tg(i),ws(i):next
  122 fori=1to10:readra$(i):next
  123 fori=0to5:readtm$(i),tk(i),tr(i):next
  124 fori=1to10:readp(i):next
  125 fori=1to3:readts$(i):next:fori=1to5:readtg$(i):next
  126 fori=0to1:readag$(i):next
  127 fori=0to2:readlm$(i):next:lm=1
  150 sysbl,"karte":sysrc,br,br+999,1024,55:sysrc,fr,fr+999,ad,55
  151 poke211,13:poke214,2:syscs:print"{dish}{rvon}{blk}  m a f i a  "
  152 printtab(9)"{down}{rvon} (c) 1986 by igelsoft "
  153 poke211,11:poke214,22:syscs:print"{rvon} chicago, 1925... "
  154 getx$:ifx$=""goto154
  155 poke53265,peek(53265)or16:return
  170 pokera,9:pokera+1,9:input"{clr}{down}{blk} spielende (1928-1978):";x$:x9=int(val(x$))
  172 ifx9<1928orx9>1978goto170
  175 pokera,9:pokera+1,9:input"{clr}{down}{blk} punktewertigkeit (0.1 - 2):";x$:x8=val(x$)
  176 ifx8<0.1orx8>2goto175
  199 return
  200 rem
  205 pokera,9:pokera+1,9:input"{clr}{down}{blk} spieleranzahl:";x$:sz=val(x$)
  206 ifsz<1orsz>4goto205
  210 fori=1tosz:print"{clr}{down}{down}{down}{down}{down}{down} name spieler"i"{left}:";:x=15:gosub290:sp$(i)=x$
  215 print"{down} bandenname:";:x=15:gosub290:bn$(i)=x$
  220 gosub300:po(i)=18:ra(i)=1:nr(i)=1:next:return
  290 x=peek(211):y=peek(214):x$="":inputx$
  291 ifx$=""orlen(x$)>13thenpoke211,x:poke214,y:syscs:goto290
  292 return
  300 gz(i)=1:gn$(i,1)=sp$(i):print"{clr}{down}eigenschaften:"
  310 print"{down}kraft:";:gosub350:kr=x
  311 print"{down}intelligenz:";:gosub350:in=xor30
  312 print"{down}brutalitaet:";:gosub350:bt=x
  313 print"{down}energie: 5":en=5:a=i:b=1:gosub1365
  315 ka(i)=int(rnd(1)*5)*500+5000:ifpeek(53247)=1thenka(i)=500000
  316 print"{down}kapital:";ka(i)"$":goto1100
  350 x=int(rnd(1)*9)*5+10 :y=peek(211):print"   {left}{left}{left}"x;:poke211,y:syscs
  355 getx$:ifx$=""goto350
  360 print:return
 1000 sp=0:ja=1925
 1010 sp=sp+1:ifsp=sz+1thensp=1:gosub4500:ja=ja+1/12:ifint(ja)=x9goto40100
 1011 gosub4000:ifra(sp)=10andx5%(sp)>0andx6%(sp)>0thensyslh,"sieg-pic":goto40000
 1012 ms=tr(tm(sp)):nr(sp)=ra(sp):ll(sp)=0:ifjo(sp)thengosub25000:goto1010
 1013 gf(sp)=int(gf(sp)*100)/100:ifgs(sp)thengosub1500:goto1010
 1015 pokera,0:pokera+1,0:print"{clr}{down}{red}{rvon} "sp$(sp)" - "bn$(sp)" "
 1016 print"{down}kapital:"ka(sp);tab(25)"jahr:";:x=int(ja):printmid$(str$(x),2);
 1017 print"-"mid$(str$(1+int((ja-x)*12)),2)
 1020 print"{down}{down}{gry3}was willst du tun:":print"{down}{down}1 - uebersicht"
 1021 print"{down}2 - durch die stadt gehen":print"{down}3 - bandenkrieg"
 1022 print"{down}4 - naechster spieler":print"{down}{down}{red}grafik laden: "lm$(lm)"  [f1]{gry3}"
 1030 getx$:x=val(x$):if(x<1orx>4)andx$<>"{f1}"goto1030
 1031 ifx=4goto1010
 1032 ifx$="{f1}"thenlm=lm+1+3*(lm=2):goto1015
 1035 onxgosub1200,2000,27000
 1045 ifms>0goto1015
 1050 goto1010
 1100 print"{down}taste druecken!":poke198,0:wait198,1:poke198,0:return
 1110 print"ok (j/n)?"
 1115 getx$:ifx$=""or(x$<>"j"andx$<>"n")goto1115
 1120 return
 1125 print"{down}du hast zu wenig kies!":goto1100
 1130 ifgz(sp)=0theny=0:return
 1135 fori=1togz(sp):print"{down}"mid$(str$(i),2)" ";:a=sp:b=i:gosub1300
 1140 poke198,0:wait198,1:nexti:poke198,0
 1145 input"{down}nummer:";y:ify>gz(sp)thenprint"{up}{up}";:goto1145
 1150 ify=0thenreturn
 1155 a=sp:b=y:gosub1350:return
 1160 gf(sp)=gf(sp)+(x*x8):ifgf(sp)>100thengf(sp)=100
 1161 ifgf(sp)<0thengf(sp)=0
 1165 nr(sp)=int(gf(sp)/11.1)+1:return
 1190 pokesi+4,0:pokesi+11,0:pokesi+18,0:pokesi+24,0:pokesi+5,0:pokesi+6,0
 1191 return
 1200 print"{clr}{down}{rvon}uebersicht fuer "sp$(sp)":"
 1208 ifpeek(53247)=1thenprintfre(0)
 1209 print"{down}{down}punkte: "gf(sp)
 1210 print"{down}rang: "ra$(ra(sp))
 1215 print"{down}transportmittel: "tm$(tm(sp))" "ms"s"
 1219 print"{down}alkohol:"ta(sp)"faesser"
 1220 ifag(sp)=0goto1225
 1221 print"{down}gegenstaende: ";:fori=0to1:if(ag(sp)and(2^i))<>0thenprintag$(i)",";
 1222 next:print"{left} "
 1225 print"{down}schmiergelder:"pl(sp)"mon.":print"{down}bezahlte miete:"um(sp)"mon."
 1230 print:gosub1100:print"{clr}{down}gangster:":ifgz(sp)=0thenprint"{down}keine!":goto1100
 1235 fori=1togz(sp):print:a=sp:b=i:gosub1300:poke198,0:wait198,1
 1240 next
 1245 goto1100
 1300 r=peek(199):l=peek(211):printgn$(a,b):x$=ge$(a,b):poke211,l:syscs
 1310 poke211,l:syscs:poke199,r
 1315 print"e"left$(x$,2)" k"mid$(x$,3,2)" i"mid$(x$,5,2)" b"right$(x$,2)
 1320 poke199,r:poke211,l:syscs:print"w:"wa$(gw(a,b)):return
 1350 x$=ge$(a,b):en=val(left$(x$,2)):kr=val(mid$(x$,3,2)):in=val(mid$(x$,5,2))
 1360 bt=val(right$(x$,2)):return
 1365 ge$(a,b)="":x$=mid$(str$(en),2):gosub1385
 1375 x$=mid$(str$(kr),2):gosub1385:x$=mid$(str$(in),2):gosub1385
 1380 x$=mid$(str$(bt),2):gosub1385:return
 1385 iflen(x$)=1thenx$="0"+x$
 1390 ge$(a,b)=ge$(a,b)+x$:return
 1400 rem
 1405 y=peek(214):fori=1to3:poke211,x:syscs:print"{rvon}                  ":next
 1410 poke211,x:poke214,y:syscs:return
 1450 poke214,14:syscs:print"{rvon}                                       ":return
 1500 gs(sp)=gs(sp)-1:pokera,0:pokera+1,6
 1510 print"{clr}{down}{gry3}du sitzt im knast."gs(sp)+1"monat(e) hast"
 1515 print"{down}du noch vor dir...":goto1100
 2000 pokera,9:pokera+1,15:sysrc,1024,2023,br,55:sysrc,ad,ad+999,fr,54
 2002 poke646,0:iftp(sp)=3thenpokebr+569,135:pokefr+569,6
 2003 iftp(sp)=5thenpokebr+861,130:pokefr+861,2
 2005 ifms<=0thenreturn
 2006 sysi1,br+po(sp),fr+po(sp),193,0,156,12
 2010 getx$:ifx$=""goto2010
 2015 x=0:ifx$=":"thenx=-1:goto2030
 2016 ifx$=";"thenx=1:goto2030
 2017 ifx$="@"thenx=-40:goto2030
 2018 ifx$="/"thenx=40:goto2030
 2019 ifx$="_"thensysie:return
 2029 goto2010
 2030 p=br+po(sp)+x:ifp<brorp>br+999goto2010
 2035 sysie:ifpeek(p)<>156goto2045
 2040 po(sp)=po(sp)+x:ms=ms-1
 2041 ifms/20=int(ms/20)andint(rnd(1)*5)=0andra(sp)>3thengosub6000:goto2060
 2042 goto2005
 2045 ifpo(sp)+x=569thenla=13:ln=1:gosub23000:goto2060
 2046 ifpo(sp)+x=861thenla=14:ln=1:gosub24000:goto2060
 2050 syslc,p:la=peek(ua+1):ln=peek(ua+2):ifla=0goto2005
 2055 gosub3000:ll(sp)=20*la+ln
 2060 ms=ms-5:ifms>0goto2000
 2065 return
 3000 ifla=9or(lm=1andlm(la)<>0)goto3010
 3001 iflm=0orlm=1goto3007
 3005 pokera,0:pokera+1,0:print"{clr}":poke211,9:poke214,11:syscs
 3006 print"{wht}grafik laden (j/n) ?":gosub1115:ifx$="n"goto3010
 3007 syslh,lk$(la)+"-pic":lm(la)=-1
 3008 ifla=13goto3045
 3010 ifll<>lagoto3015
 3011 pokera,rf:pokera+1,hf:poke646,cf
 3012 sysrc,ad+1000,ad+1999,br,54:sysrc,ad+2000,ad+2999,fr,54:goto3040
 3015 open1,8,2,lk$(la)+",s,r"
 3020 input#1,rf:input#1,hf:input#1,cf:pokera,rf:pokera+1,hf:poke646,cf
 3025 input#1,x$:print"{clr}{down}{rvon} "x$": ":input#1,x$:print"{down}{down}"x$:input#1,aw
 3030 print:fori=1toaw:input#1,x$:print"{down}"mid$(str$(i),2)" "x$
 3035 next:close1:sysrc,br,br+999,ad+1000,55:sysrc,fr,fr+999,ad+2000,55:ll=la
 3040 getx$:w=val(x$):ifw<1orw>awgoto3040
 3045 ifw=awthenms=ms-5:return
 3050 print"{clr}"
 3100 ifla>10goto3110
 3105 onlagoto10000,12000,13000,14000,15000,16000,17000,18000,19000,20000
 3110 onla-10goto21000,22000,23000
 4000 rem
 4005 pokera,8:pokera+1,8:print"{clr}":poke211,15-len(sp$(sp))/2:poke214,10:syscs
 4006 print"{blk}spieler "sp$(sp):printtab(10)"{down}ist an der reihe..."
 4010 sysso,9:fort=1to1000:next:fori=1togz(sp)
 4015 a=sp:b=i:gosub1350:en=en+int(kr/10)+1
 4020 x=2+int(kr/4)+int(bt/4):ifen>xthenen=x
 4025 gosub1365:nexti
 4030 ifra(sp)<>nr(sp)thenra(sp)=nr(sp):gosub4200
 4040 ifkr(sp)andgs(sp)=0thengosub4300
 4041 ifkg(sp)<>0andkk(sp)<>0thengosub4400
 4045 ifum(sp)=0goto4050
 4046 um(sp)=um(sp)-1:ifum(sp)=0thenum(sp)=1:gosub4600
 4050 pl(sp)=pl(sp)+(pl(sp)>0)
 4055 ifint(rnd(1)*8)=0thenag(sp)=ag(sp)and254
 4056 ifint(rnd(1)*8)=0thenag(sp)=ag(sp)and253
 4060 iftp(sp)=4thengosub31000
 4090 return
 4200 sysbl,"want":poke211,20-int(len(bn$(sp))/2):poke214,8:syscs:print"{rvon}"bn$(sp)
 4210 poke211,19:poke214,15:syscs:print"{rvon}"sp$(sp)"."
 4215 poke211,25:poke214,20:syscs:print"{rvon}"mid$(str$(gf(sp)),2)" p."
 4220 poke211,14:syscs:print"{rvon}"ra$(ra(sp)):poke198,0:wait198,1:poke198,0:return
 4300 pokera,11:pokera+1,11:print"{clr}{down}{rvon}{yel} "sp$(sp)"! "
 4305 kz(sp)=kz(sp)+(kz(sp)>0):ifkz(sp)=0goto4350
 4306 print"{down}du hast"kr(sp)"$ schulden. dir bleiben"
 4308 print"{down}noch"kz(sp)+1"monat(e), sie"
 4309 print"{down}abzuzahlen!":goto1100
 4350 print"{down}der kredithai hetzt seine eintreiber":print"{down}auf dich...":gosub1100
 4355 bn$(0)="eintreiber":w=3:e=30:gz(0)=5:kf$="ks":gosub5000:ifs=1thenreturn
 4365 print"{clr}{down}die kerle nehmen dein ganzes geld mit":print"{down}und verschwinden..."
 4370 ka(sp)=0:kr(sp)=0:kz(sp)=0:goto1100
 4400 pokera,11:pokera+1,11
 4405 ifint(rnd(1)*3)goto4410
 4406 print"{clr}{down}{lgrn}in deinem kreditinstitut herrscht":print"{down}flaute.":goto1100
 4410 p=int(rnd(1)*kk(sp)/20+kk(sp)/10):ka(sp)=ka(sp)+p
 4415 print"{clr}{down}{lgrn}dein kreditgeschaeft floriert und du"
 4420 print"{down}machst"p"$ einnahmen.":goto1100
 4500 pokera,2:pokera+1,2:print"{clr}{down}{rvon}{blk} spielstand ";:x=int(ja)
 4501 printmid$(str$(x),2)"-"mid$(str$(1+int((ja-x)*12)),2)" "
 4505 print"{down}spieler:        kapital: punkte:"
 4510 fori=1tosz:print"{down}"sp$(i);tab(15);ka(i)"$";tab(26);gf(i):next
 4515 print:goto1100
 4600 pokera,2:pokera+1,2
 4605 p=int(rnd(1)*100)+200:ifp>ka(sp)thenp=ka(sp):ifp=0goto4650
 4610 print"{clr}{down}{gry3}du hast deine miete nicht puenktlich"
 4615 print"{down}gezahlt. man hat dir moebel im wert von"
 4620 print"{down}{left}"p"$ gepfaendet!":ka(sp)=ka(sp)-p:goto1100
 4650 print"{clr}{down}{gry3}deine wohnung wird dir gekuendigt!"
 4651 print"{down}deine gangster suchen sich einen anderen":print"boss...":gz(sp)=1
 4652 goto1100
 5000 fori=1togz(0):gw(0,i)=w:ec(i)=e:next
 5010 ks(1)=sp:ks(2)=0:goto30000
 6000 pokera,14:pokera+1,14:print"{clr}{down}{rvon}{blk} strassensperre!!! "
 6010 print"{down}ausweiskontrolle! der polizist verlangt":print"{down}deine papiere!"
 6015 fort=1to2000:next:ifint(rnd(1)*3)=0goto6025
 6016 if(ag(sp)and2)<>0goto6030
 6017 ifta(sp)goto6035
 6018 if(ag(sp)and1)<>0goto6025
 6020 print"{down}er hat einen steckbrief von dir!":gosub1100:goto26020
 6025 print"{down}er hat nichts zu beanstanden.":goto1100
 6030 print"{down}dein blueten-schwindel ist aufgeflogen!":gosub1100:goto26020
 6035 print"{down}der kerl hat deine alkoholfaesser ent-"
 6036 print"{down}deckt! es ist aus...":ta(sp)=0:gosub1100:goto26020
10000 rem
10005 onwgoto10010,10100
10010 ifuk(ln)<>0thenprint"'nichts mehr frei!'":goto1100
10020 p=fnm(ln):print"'gut. pro monat kostet das"p"$":print"{down}miete.'":goto10025
10025 input"{down}wieviele monate willst du mieten";x
10030 ifx=0orx<0 thennm=1:return
10035 ifka(sp)<x*pgoto1125
10040 ka(sp)=ka(sp)-x*p:uk(ln)=sp:um(sp)=um(sp)+x
10045 print"{down}'guten tag, der herr!'":goto1100
10100 ifuk(ln)<>spthenprint"du wohnst hier nicht!":nm=1:goto1100
10105 goto10020
12000 rem
12005 onwgoto12010,12100,12200,12300
12010 ifln=4orln=5goto12020
12015 ifint(rnd(1)*2)=0goto12050
12016 print"'was? alkohol? ist doch verboten!'":goto1100
12020 x=int(rnd(1)*200)+100:p=int(rnd(1)*5)+5
12021 print"'ich habe"
12022 printx"faesser fuer"p"$ pro fass.'"
12025 q=tk(tm(sp))-ta(sp):ifq<xthenx=q
12027 print"{down}wieviele willst du (0-"mid$(str$(x),2)")";:inputy
12028 ify>xthenprint"{up}{up}";:goto12027
12029 ify=0thenreturn
12030 ifka(sp)<y*pgoto1125
12035 ta(sp)=ta(sp)+y:ka(sp)=ka(sp)-p*y:x=2:gosub1160:return
12050 x=int(rnd(1)*20)+10
12055 print"'nein. vielleicht hast du aber was fuer"
12056 print"{down}mich? ich zahle"x"$ pro fass!'"
12060 print"{down}wieviel willst du abgeben (0-"mid$(str$(ta(sp)),2)")";:inputy
12061 ify>ta(sp)thenprint"{up}{up}";:goto12060
12065 ify=0thenreturn
12070 print"{down}'besorg noch mehr{$a0}(gier)!'":goto12075
12075 ka(sp)=ka(sp)+y*x:ta(sp)=ta(sp)-y:goto1100
12100 ifra(sp)>4goto12103
12101 print"als "ra$(ra(sp))" kannst du noch"
12102 print"{down}keine eigenen leute haben!":goto1100
12103 fori=1to5:ifuk(i)=spgoto12105
12104 nexti:print"du brauchst eine unterkunft fuer die    {down}gangster!":goto1100
12105 ifgz(sp)=10thenprint"{down}maximal 10 gangster!":goto1100
12106 y=0:fori=1to30:y=y-(sg(i)=0):next:ify>3theny=3
12107 x=int(rnd(1)*(y+1)):ifx=0orln=3thenprint"es ist niemand da.":goto1100
12108 fori=1tox
12110 g(i)=int(rnd(1)*30)+1:ifsg(g(i))goto12110
12111 ifi=1goto12115
12112 forj=1toi-1:ifg(j)=g(i)goto12110
12113 nextj
12115 iffnwe(g(i))goto12120
12116 print"{clr}{down}ein gangster kommt, um mit dir zu    {down}sprechen...":goto12125
12120 print"{clr}{down}eine dame scheint sich fuer deine"
12121 print"{down}gang zu interessieren..."
12125 open1,8,2,"gan"+mid$(str$(g(i)),2)+",s,r":input#1,gn$:input#1,gw
12126 input#1,ge$:input#1,p:p=p+1000:input#1,x$:close1
12130 y$="er":iffnwe(g(i))theny$="sie"
12131 print"{down}"y$" wird "gn$" genannt.":print"{down}"x$"."
12135 print"{down}"y$" verlangt"p"$ vorschuss, wenn"
12136 printy$" sich dir anschliessen soll.":print:gosub1110:ifx$="n"goto12175
12140 ifka(sp)<pthengosub1125:goto12175
12145 ifgz(sp)=10goto12005
12160 ka(sp)=ka(sp)-p:gz(sp)=gz(sp)+1:gn$(sp,gz(sp))=gn$
12165 gw(sp,gz(sp))=gw:ge$(sp,gz(sp))="05"+ge$:sg(g(i))=1
12170 print"{down}nun hast du"gz(sp)"gangster!":gosub1100
12175 nexti:return
12200 ifra(sp)>3goto12210
12205 print"'du bist noch zu unerfahren!'":goto1100
12210 ifint(rnd(1)*3)thenprint"'leider habe ich nichts fuer dich!'":goto1100
12215 p=1000+int(rnd(1)*3)*500:print"{down}'ich habe etwas gehoert...aber das"
12216 print"{down}kostet dich"p"$. ";:gosub1110:ifx$="n"thenreturn
12220 ifka(sp)<pgoto1125
12225 ka(sp)=ka(sp)-p:tp(sp)=int(rnd(1)*5)+1
12226 ontp(sp)goto12230,12235,12240,12245,12250
12230 print"{clr}{down}'im postzug soll demnaechst eine grosse"
12231 print"{down}ladung diamanten transportiert werden!'":goto1100
12235 print"{clr}{down}'die bank an der hauptstrasse erhaelt"
12236 print"{down}in naechster zeit 500 kg goldbarren!'":goto1100
12240 print"{clr}{down}'ein geldtransport wird durch unsere"
12241 print"{down}stadt fahren. es ist ein blauer wagen"
12242 print"{down}wagen, du wirst ihn sehen!'":goto1100
12245 print"{clr}{down}'mit 5000 $ kannst du dich an einem"
12246 print"{down}grossen waffenschmuggel beteiligen!'":print
12247 gosub1110:ifx$="n"thentp(sp)=0:return
12248 ifka(sp)<5000thentp(sp)=0:goto1125
12249 ka(sp)=ka(sp)-5000:print"{down}'du wirst es nicht bereuen...'":goto1100
12250 print"{clr}{down}'psst...ich habe da einen sehr wichtigen"
12251 print"auftrag! der buergermeister soll umge-"
12252 print"{down}legt werden, er hat aber leibwaechter!'":goto1100
12300 ifra(sp)<4goto12302
12301 print"als '"ra$(ra(sp))"' findest du was":print"{down}besseres!":goto1100
12302 ifint(rnd(1)*5)=0thenprint"niemand hat einen job fuer dich.":goto1100
12305 x=int(rnd(1)*4)+1:onxgoto12306,12310,12315,12320
12306 print"der inhaber der bahnhofskneipe will"
12307 print"{down}dich fuer 3 monate als rausschmeisser"
12308 print"{down}anstellen.":jd(sp)=3:p=int(rnd(1)*1000)+2000:goto12330
12310 print"du kannst 2 monate als croupier in geor-"
12311 print"ges spielhoelle arbeiten.":p=int(rnd(1)*500)+1000:jd(sp)=2:goto12330
12315 print"das 'le roi'-hotel sucht einen tuer-"
12316 print"{down}steher fuer 2 monate.":p=int(rnd(1)*500)+2000:jd(sp)=2:goto12330
12320 print"du erhaelst das foto eines mannes, der"
12321 print"{down}im 'red house' abgestiegen ist. du":print"{down}sollst ihn umlegen!"
12322 p=int(rnd(1)*500)+2000:jd(sp)=1
12330 print"{down}du bekommst dafuer"p"$. ";:gosub1110:ifx$="n"thenreturn
12335 print"{down}du hast den job!":jo(sp)=x:jl(sp)=p:ms=0:goto1100
13000 rem
13005 onwgoto13010,13100
13010 print"{clr}{down}ok, wir haben folgendes:":print
13011 ifln=1thena=3:b=7:ifint(rnd(1)*3)=0andra(sp)>5thengosub13090
13012 ifln=2thena=1:b=5
13013 ifln=3thena=1:b=4
13015 fori=atob:printmid$(str$(i),2)" - "wa$(i);wp(i)"$":next
13020 input"{down}ihre wahl:";x:ifx=0thenreturn
13021 ifx<aorx>bthenprint"{up}{up}";:goto13020
13025 ifka(sp)<wp(x)thengosub1125:goto13010
13035 gosub13500:print"{clr}{down}fuer welchen gangster:":gosub1130:ify=0goto13010
13050 ifin<40andx>5thenprint"{down}zu wenig intelligenz!":gosub1100:goto13035
13055 ifkr<20and(x=2orx=3)thenprint"{down}zu wenig kraft!":gosub1100:goto13035
13060 ifbt<40and(x=3orx>6)thenprint"{down}nicht brutal genug!":gosub1100:goto13035
13065 ifgw(sp,y)=0thenq=0:gf(sp)=gf(sp)-x8*1*(gf(sp)<100):goto13075
13070 q=int(wp(gw(sp,y))/1.5):print"{clr}{down}der haendler bietet dir"q"$ fuer"
13071 print"{down}deine alte waffe. ";:gosub1110:ifx$="n"goto13010
13072 ifx>gw(sp,y)thengf(sp)=gf(sp)-x8*1*(gf(sp)<100):goto13075
13073 gf(sp)=gf(sp)+x8*2*(gf(sp)>0)
13075 ka(sp)=ka(sp)+q-wp(x):gw(sp,y)=x
13080 print"{down}du hast nun die neue waffe!":goto1100
13090 print"...brandheiss! eine ladung army-hand-"
13091 print"{down}granaten ist gerade eingetroffen!":b=8:print:return
13100 ifgn$(sp,1)=""thenprint"leider hast du nicht einen gangster!":goto1100
13101 print"wen soll ich trainieren:"
13102 gosub1130:ify=0thenreturn
13103 ifra(sp)<5goto13110
13105 print"{down}(s)chiesstand oder (t)rainingscamp ?"
13106 getx$:ifx$<>"s"andx$<>"t"goto13106
13107 ifx$="t"goto13150
13110 p=800+200*ra(sp):print"{down}das kostet dich"p"$. ";
13115 gosub1110:ifx$="n"thenreturn
13116 ifka(sp)<pgoto1125
13120 print"{clr}{down}'dein gangster uebt an meinem schiess-"
13121 print"{down}stand...'"
13125 ka(sp)=ka(sp)-p:kr=kr+5:ifkr>99thenkr=99
13126 in=in+3-2*(ln=1):ifin>99thenin=99
13127 bt=bt+2-3*(ln=2):ifbt>99thenbt=99
13130 gosub1365:x=1:gosub1160:goto1100
13150 p=2500+500*ra(sp):print"{down}das kostet dich"p"$. ";
13155 gosub1110:ifx$="n"thenreturn
13160 ifka(sp)<pgoto1125
13165 print"{clr}{down}"gn$(a,b)" ist im trainingscamp...":fort=1to2000:next
13170 ka(sp)=ka(sp)-p:in=in+fnr(0):ifin>99thenin=99
13171 bt=bt+fnr(0):ifbt>99thenbt=99
13172 kr=kr+fnr(0):ifkr>99thenkr=99
13175 gosub1365:x=2:gosub1160:print"{down}...da ist er wieder!":goto1100
13500 print"{clr}{down}{rght}{rvon}{blk} waffe: "wa$(x)" {gry3}":print:forj=1to6:print" {rvon}      ":nextj
13505 pokev,32:pokev+1,78:pokev+39,0:pokev+23,1:pokev+29,1
13510 pokepo,23+x:pokev+21,1:print"{home}{down}{down}{down}"tab(8)"{blk}preis:"wp(x)"$"
13515 printtab(8)"{down}treffgenauigkeit: "ts$(int(ts(x)/2))
13520 printtab(8)"{down}wirkung: "tg$(int(tg(x)/4)+1)
13525 print"{down}{down} taste druecken!":poke198,0:wait198,1:poke198,0:pokev+21,0:return
14000 rem
14005 onwgoto14006,14100
14006 print"{clr}{down}der verkaeufer zeigt dir eine auswahl"
14007 print"{down}der modelle. zum kauf die entsprechende"
14008 print"{down}nummerntaste (0=ende) druecken!":gosub1100:print"{clr}";
14010 x=2:ifln=2thenx=x+1
14011 fori=0tox:y=x-1
14012 fori=0tox:print:forj=1to5:print" {gry3}{rvon}        {blk}":nextj
14013 pokepo+i,20+i:pokev+2*i,38:pokev+1+2*i,60+48*i:pokev+39+i,0
14014 printtab(10)"{up}{up}{up}{up}{up}"chr$(49+i)" "tm$(i+1):printtab(11);3000+1000*i"${down}{down}{down}"
14015 next:y=2^(x+1)-1:pokev+23,y:pokev+29,y:pokev+21,y
14020 getx$:ifx$=""goto14020
14025 y=val(x$):ify=0thenpokev+21,0:return
14030 if(y-1)>xgoto14020
14035 pokev+21,0:print"{clr}":p=3000+1000*(y-1):ifka(sp)<pthengosub1125:goto14006
14040 iftm(sp)=0thenq=0:goto14050
14045 q=1000+1000*tm(sp):iftm(sp)=5thenq=1000
14046 print"man bietet dir"q"$ fuer deine alte"
14047 print"{down}schaukel. ";:gosub1110:ifx$="n"goto14006
14050 ka(sp)=ka(sp)-p+q:ms=tr(y)-tr(tm(sp))+ms:tm(sp)=y
14051 print"{clr}{down}der verkaeufer reicht dir schluessel"
14052 print"{down}und papiere.":goto1100
14100 ifln<>4andint(rnd(1)*3)<>0thenprint"es sind zuviele leute hier!":goto1100
14101 print"wer soll den wagen aufbrechen:":gosub1130:ify=0thenreturn
14110 ifint(rnd(1)*(in/40+kr/30))=0goto14120
14115 print"{clr}{down}yeah!{$a0}die karre ist offen! du machst"
14116 print"{down}dich damit aus dem staub.";:iftm(sp)=0thenprint:goto14118
14117 print"{left} und musst lei- {down}der deinen alten wagen stehen lassen."
14118 tm(sp)=5:goto1100
14120 print"{clr}{down}leider erwischt dich der besitzer der   {down}karre!":gosub1100
14125 bn$(0)="wagenbesitzer":gz(0)=1:e=30:w=5:kf$="ks":gosub5000:ifs=2goto26020
14130 print"{clr}{down}nach dem mord an dem besitzer musst du"
14131 print"{down}verschwinden und den wagen stehen":print"{down}lassen!":goto1100
15000 rem
15005 onwgoto15010,15050,15100,15200,15300
15010 ifkr(sp)thenprint"zahle erstmal deine alten schulden ab!":goto1100
15015 print"wieviel geld willst du leihen":input"{down}(0-5000 $)";x$:x=val(x$)
15020 ifx=0thenreturn
15021 ifx<0orx>5000thenprint"{clr}":goto15015
15025 print"{down}du hast 6 monate zeit, die schulden"
15026 print"{down}zurueck zu zahlen!"
15030 kr(sp)=kr(sp)+x:ka(sp)=ka(sp)+x:kz(sp)=6:goto1100
15050 print"wieviel willst du zurueckzahlen"
15051 print"{down}(0-"mid$(str$(kr(sp)),2)")";:inputx$:x=val(x$):ifx=0thenreturn
15055 ifx<0orx>kr(sp)thenprint"{clr}":goto15050
15060 ifka(sp)<xgoto1125
15065 kr(sp)=kr(sp)-x:ka(sp)=ka(sp)-x:ifkr(sp)=0goto15075
15070 print"{down}ok, du hast jetzt noch"kr(sp)"$":print"{down}schulden!":goto1100
15075 kz(sp)=0:print"{down}du hast die schulden zurueckgezahlt!":goto1100
15100 ifkg(sp)=lngoto15150
15105 ifkg(sp)thenprint"du hast schon ein kreditgeschaeft!":goto1100
15106 ifkr(sp)thenprint"zahle erstmal deine eigenen schulden!":goto1100
15107 fori=1tosz:ifkg(i)<>lnthennext:goto15110
15108 print"der laden gehoert "sp$(i)"!":goto1100
15110 p=int(rnd(1)*11)*100+5000:print"der jetzige besitzer verlangt"p"$."
15111 print:gosub1110:ifx$="n"thenreturn
15115 ifka(sp)<pgoto1125
15120 ka(sp)=ka(sp)-p:kg(sp)=ln:print"{down}der laden gehoert nun dir!":gosub1100
15125 goto15200
15150 p=int(rnd(1)*11)*100+4500:print"ein kaeufer bietet dir"p"$ fuer den"
15151 print"{down}laden. willst du verkaufen (j/n) ?":gosub1115:ifx$="n"thenreturn
15155 ka(sp)=ka(sp)+p:kg(sp)=0:return
15200 ifkg(sp)<>lnthenprint"dieser laden gehoert dir nicht!":goto1100
15205 print"{clr}{down}du hast"kk(sp)"$ im kreditgeschaeft":print"{down}(maximal 5000 $)."
15206 print"{down}um wieviel dollar (- oder +) willst du"
15207 print"{down}das geldvolumen erhoehen bzw. verrin-"
15208 input"{down}gern";x$:x=val(x$):ifx=0thenreturn
15210 ifkk(sp)+x<0orkk(sp)+x>5000goto15205
15215 ifka(sp)<xgoto1125
15220 ka(sp)=ka(sp)-x:kk(sp)=kk(sp)+x:return
15300 ifkg(sp)<>lnthenprint"dieser laden gehoert dir nicht!":goto1100
15305 ifint(rnd(1)*3)<>0andkk(sp)<>0goto15310
15306 print"alle schuldner haben puenktlich ge-":print"{down}zahlt!":goto1100
15310 print"{clr}{down}einer deiner klienten hat noch geld. er"
15311 print"{down}lauert dir mit einem gewehr auf, als du {down}kassieren willst!"
15312 gosub1100:bn$(0)="schuldner":gz(0)=1:e=35:w=6:gosub5000
15315 ifs=2thenreturn
15320 p=int(rnd(1)*1000)+500:pokera,11:pokera+1,11
15321 print"{clr}{down}{yel}er hatte noch"p"$ dabei!":ka(sp)=ka(sp)+p:x=2:gosub1160:goto1100
16000 rem
16010 print"waehle, mein freund:":print"{down}1 - poker":print"2 - black jack"
16011 print"3 - roulette"
16015 input"{down}was soll's sein:";x:ifx=0thenreturn
16016 ifx<1orx>3thenprint"{up}{up}";:goto16015
16020 print"{down}du hast"ka(sp)"$. dein einsatz:":inputp:ifp=0orp<0 thenreturn
16025 ifp>ka(sp)goto1125
16026 ka(sp)=ka(sp)-p:print"{down}du begibst dich an den spieltisch..."
16030 fort=1to1000:next:ifint(rnd(1)*(1+x))=0thenp=int(p*(.5+x)):goto16040
16035 print"{down}leider verloren!":goto1100
16040 print"{down}du hast"p"$ gewonnen!":ka(sp)=ka(sp)+p:goto1100
17000 rem
17005 ifra(sp)>1goto17007
17006 print"'verschwinde, du milchgesicht!'":goto1100
17007 ifll(sp)<>20*la+lngoto17010
17008 print"{clr}{down}vor dem laden erwartet dich die"
17009 print"{down}poliyei!":gosub1100:kf$="ks":goto26000
17010 onwgoto17015,17100,17200,17300
17015 print"{clr}":a=sp:b=1:gosub1350
17016 ifbt<30thenprint"du schindest keinen eindruck...":print:goto17020
17017 ifln=1orln=7orln=9goto17500
17020 print"der kerl ruft die polizei!!!":gosub1100:kf$="ks":goto26000
17100 ifln=1orln=4orln=5goto17500
17105 print"{clr}{down}'sorge doch selbst fuer deine alte!'":goto1100
17200 ifln=2orln=3orln=8goto17500
17205 print"{clr}{down}'das wird aber narben-jack interes-"
17206 print"{down}sieren!'":gosub1100
17210 bn$(0)="jack's gang":gz(0)=3-2*(gz(sp)>5):w=7:e=30:kf$="ksgl":gosub5000
17215 ifs=2thenreturn
17220 pokera,4:pokera+1,4:print"{clr}{down}jack verzieht sich erstmal aus diesem"
17221 print"{down}gebiet...":gosub1100
17225 goto17500
17300 a=sp:b=1:gosub1350:if(ln=1orln=3orln=6)andin>=30goto17500
17305 print"{clr}{down}'ach, die alte bullen-masche! damit"
17306 print"{down}neppt ihr keinen mehr!'":goto1100
17500 x=2:gosub1160:ifint(rnd(1)*3)=0goto17530
17505 p=int(rnd(1)*200)+800-300*(ln=2)-200*(ln=7)-200*(ln=9)+600*(w=2)
17510 onwgoto17511,17515,17520,17525
17511 print"{clr}{down}'i..i..ich z..zahle ja schon! hi..hier"
17512 print"{down}sind"p"$!'":goto17550
17515 print"{clr}{down}'deine arme ma (schnief)! gib ihr die":print"{down}"p"$ hier!'"
17516 ll(sp)=0:goto17550
17520 print"{clr}{down}'ich habe"p"dollar, reicht das?'":goto17550
17525 print"{clr}{down}'verdammt! wer hat mir nur blueten"
17526 print"{down}fuer"p"$ angedreht...?'":goto17550
17530 p=int(rnd(1)*100)+100:print"{clr}{down}'ich habe leider nur"p"$!"
17550 ka(sp)=ka(sp)+p:print"{down}{down}was machst du:":print"{down}1 angebotenes geld nehmen"
17551 print"{down}2 laden demolieren":print"{down}3 besitzer fertigmachen"
17555 getx$:ifx$<"1"orx$>"3"goto17555
17560 onval(x$)goto17565,17570,17580
17565 return
17570 ifln<>2andln<>6andln<>7andln<>8goto17575
17571 print"{clr}{down}'saubande! das werdet ihr buessen!'"
17572 print"{down}(der kerl winkt ein paar schlaeger":print"{down}heran!)":gosub1100
17573 bn$(0)="schlaeger":gz(0)=5:w=3:e=20:kf$="ksgl":gosub5000:ifs=2thenreturn
17575 pokera,4:pokera+1,4:p=int(rnd(1)*100)+300
17576 print"{clr}{down}{blk}du hast kleinholz aus dem laden"
17577 print"{down}gemacht. in der kasse waren"p"$!"
17578 ka(sp)=ka(sp)+p:x=1:gosub1160:goto1100
17580 ifln<>1andln<>4goto17590
17585 print"{clr}{down}er verspricht dir einen schoenen grab-"
17586 print"{down}stein und laedt seine "wa$(7)"!":gosub1100
17587 bn$(0)="ladenbesitzer":gz(0)=1:w=7:e=30:kf$="ksgl":gosub5000
17588 ifs=2thenreturn
17590 p=int(rnd(1)*100)+200:pokera,4:pokera+1,4
17591 print"{clr}{down}{blk}der aufmuepfige kerl ist hin."p"$"
17592 print"{down}hatte er der tasche!":x=1:gosub1160:goto17578
18000 rem
18010 onwgoto18035,18015
18015 print"ein u-bahn-ticket kostet dich"
18020 print"{down}50 $. ";:gosub1110:ifx$="n"thenreturn
18025 ifka(sp)<50goto1125
18030 ka(sp)=ka(sp)-50
18035 print"{clr}{down}welchen spieler setzt du als dieb ein:":gosub1130:ify=0thenreturn
18039 x=1:gosub1160:print"{clr}{down}du stiehlst...":fort=1to500:next
18040 ifint(rnd(1)*15)=10goto18052
18041 ifint(rnd(1)*(in/10))goto18045
18042 print"{down}...nichts! denn du wirst erwischt!":gosub1100:goto26020
18045 onint(rnd(1)*4)-(w=2)-(la<>9)goto18047,18048,18049,18050,18051
18046 print"{down}...eine handtasche - nichts wertvolles!":goto1100
18047 print"{down}...'nen fotoapparat! er bringt 50 $.":ka(sp)=ka(sp)+50:goto1100
18048 print"{down}...eine falsche perlenkette!":goto1100
18049 print"{down}...eine armbanduhr fuer 100 $!":ka(sp)=ka(sp)+100:goto1100
18050 print"{down}...'ne brieftasche mit 500 $!":ka(sp)=ka(sp)+500:goto1100
18051 print"{down}...einen diamanten! der ist 800 $ wert!":ka(sp)=ka(sp)+800:goto1100
18052 print"{down}...eine anleitung -der safeknacker- ??!":s9(sp)=5:goto1100
19000 rem
19005 onwgoto19010,19050,19015
19010 ln=5:la=2:goto3000
19015 iftp(sp)<>1thenprint"kein postzug zu sehen...":goto1100
19016 ifgz(sp)<3thenprint"du hast zu wenig gangster!":tp(sp)=0:goto1100
19020 syslh,"pzug-pic"
19025 pokera,11:pokera+1,8:print"{clr}{down}{blk}du stuermst in den panzerwaggon und"
19026 print"{down}beginnst gerade einzusacken, als du auf"
19027 print"{down}drei nette herren aufmerksam wirst...":gosub1100
19030 bn$(0)="wachen":gz(0)=3:w=7:e=30:kf$="kpzug":gosub5000:ifs=2goto26020
19040 goto20050
19050 w=1:goto18035
20000 rem
20003 ifra(sp)<3thenprint"werde erst '"ra$(3)"'!":goto1100
20004 ifll(sp)=20*la+lngoto17008
20005 onwgoto20009,20100
20009 ifgz(sp)=1thenprint"du brauchst einen begleiter!":goto1100
20010 ifint(rnd(1)*3)=0goto20050
20011 print"leider hast du die drei wachmaenner am"
20012 print"{down}eingang uebersehen...":gosub1100:gz(0)=3-(ln=1)
20015 bn$(0)="wachmaenner":e=30:w=6:kf$="kb":gosub5000:ifs=2goto26020
20050 p=int(rnd(1)*3000)+4000-500*(la=10andln=1):x=tp(sp)
20051 if(x=1andla=9)or(x=2andla=10andln=2)or(x=3andla=13)thentp(sp)=0:p=p+3000
20055 pokera,8:pokera+1,8:print"{clr}{down}{blk}du hast es geschafft!{$a0}deine beute"
20060 print"{down}betraegt"p"$!":ka(sp)=ka(sp)+p:x=4:gosub1160:goto1100
20100 a=sp:b=1:gosub1350:ifin>=40andkr>=15andbt>=20goto20102
20101 print"{clr}{down}{gry2}du musst noch trainieren!":goto1100
20102 pokera,0:pokera+1,0
20104 print"{clr}{down}{gry2}wer soll den kasten knacken:":gosub1130:ify=0thenreturn
20105 print"{clr}{down}'hhm. mal sehen. ich versuch's mit dem"
20106 print"{down}stethoskop...' (drehen der coderaeder"
20107 print"{down}mit f1, f3 und f5!)":gosub1100
20110 sysbl,"trs1":pokesi+24,15:fori=0to2:rd(i)=1+i:cd(i)=int(rnd(1)*10):next
20111 y=20+int(in/10)+3*(ln=1)+s9(sp):s9(sp)=s9(sp)-1:ifs9(sp)<0thens9(sp)=0
20115 getx$:x=asc(x$+chr$(0)):ifx<133orx>135goto20115
20116 x=x-133:rd(x)=rd(x)+1:ifrd(x)=10thenrd(x)=0
20120 poke211,16+3*x:poke214,7:syscs:print"{blk}{rvon}"chr$(48+rd(x))
20125 ifint(rnd(1)*(in/8))=0orrd(x)<>cd(x)thensysso,7:goto20135
20130 sysso,8:fori=0to2:ifrd(i)=cd(i)thennext:gosub1190:goto20150
20135 y=y-1:ify>0goto20115
20140 gosub1190:sysso,6:pokera,0:pokera+1,0
20141 print"{clr}{down}{gry1}'teufel...! da ist was schiefgegangen!"
20142 print"{down}es kommt jemand!'":gosub1100:kf$="kb":goto26000
20150 sysbl,"trs2":poke198,0:wait198,1:poke198,0:x=-1:gosub1160:goto20050
21000 rem
21005 onwgoto26045,21010,21100
21010 print"aha! fuer einen monat untaetigkeit"
21011 input"{down}nehme ich 1000 $. wieviele monate:";x:p=1000*x:ifx=0thenreturn
21015 ifka(sp)<pthenprint"{down}sie sind leider nich fluessig!":goto1100
21020 ka(sp)=ka(sp)-p:pl(sp)=pl(sp)+x+1:x=2:gosub1160
21025 print"{down}danke sehr! nehmen sie den hinter-"
21030 print"{down}ausgang!":po(sp)=911:goto1100
21100 x=1:fori=1tosz:ifgs(i)theng(x)=i:x=x+1
21105 next:ifint(rnd(1)*3)=0andra(sp)>4theng(x)=5:x=x+1:sp$(5)="irgendjemanden"
21110 ifx=1thenprint"es ist niemand inhaftiert.":goto1100
21115 print"wen willst du befreien:"
21116 x=x-1:fori=1tox:print"{down}"mid$(str$(i),2)" "sp$(g(i)):next
21120 getx$:ifx$=""orval(x$)>xgoto21120
21125 g=val(x$):ifg=0thenreturn
21130 p=500*int(rnd(1)*5)+3000:print"{clr}{down}du brauchst"p"$, um die waerter zu"
21131 print"{down}bestechen. ";:gosub1110:ifx$="n"thenreturn
21135 ifka(sp)<pgoto1125
21140 ka(sp)=ka(sp)-p
21200 print"{clr}{down}du konntest den gefangenen befreien!":gosub1100:ifg(g)<>5goto21250
21205 ifgz(sp)=10thenprint"{down}er bedankt sich und verschwindet...":goto1100
21210 x=gz(sp)+1:gz(sp)=x:gn$(sp,x)="knasti":gw(sp,x)=0:ge$(sp,x)="05100540"
21215 return
21250 x=g(g):print"{clr}{down}{rvon} "sp$(x)"! ":print"{down}du bist von "sp$(sp)" aus dem"
21251 print"{down}knast befreit worden. wieviel zahlst du"
21252 print"{down}ihm zum dank (0 -"ka(x)"{left}):";:inputx$:y=val(x$):ify>ka(x)goto21250
21253 ify>ka(x)ory<0thenprint"{up}{up}";:goto21252
21255 ka(x)=ka(x)-y:ka(sp)=ka(sp)+y:gs(x)=0:x=2:gosub1160:return
22000 rem
22005 onwgoto22010,22100
22010 x=gz(sp):p=1000*x:print"gut, mein sohn. ";
22011 ifx=1thenprint"fuer einen pass":goto22013
22012 print"fuer"x"paesse"
22013 print"{down}macht das"p"$. ";:gosub1110:ifx$="n"thenreturn
22014 ifka(sp)<pgoto1125
22015 print"{down}hier, noch druckfrisch, he, he!"
22020 ka(sp)=ka(sp)-p:ag(sp)=ag(sp)or1:x=1:gosub1160:goto1100
22100 print"nng...hoechst ungern! wieviel dollar"
22105 input"{down}willst du anlegen (0-5000)";q:ifq<=0thenreturn
22106 ifq>5000orq>ka(sp)thenprint"{up}{up}";:goto22105
22110 p=int(rnd(1)*q/2)+q+100:print"{down}ich gebe dir"p"$ blueten dafuer.":print
22115 gosub1110:ifx$="n"thenreturn
22120 ka(sp)=ka(sp)-q+p:ag(sp)=ag(sp)or2:x=1:gosub1160:return
23000 rem
23001 syslh,"gtp-pic"
23005 pokera,0:pokera+1,6:print"{clr}{down}{rvon}{blk} geldtransport-ueberfall "
23010 ifgz(sp)<3thenprint"{down}du hast zuwenig gangster!":tp(sp)=0:goto1100
23015 print"{down}leider wird der geldtransport von einer"
23020 print"{down}polizeieskorte begleitet!":gosub1100
23025 bn$(0)="eskorte":gz(0)=10:e=50:w=7:kf$="kgtp":gosub5000:ifs=2goto26020
23030 x=4:gosub1160:x5%(sp)=1:goto20050
24000 rem
24005 bn$(0)="leibwaechter":gz(0)=5:e=20:w=7:kf$="ks":gosub5000:ifs=2goto26020
24010 bn$(0)="buergermeister":gz(0)=1:e=30:w=1:kf$="ks":gosub5000:ifs=2goto26020
24015 pokera,0:pokera+1,6:print"{clr}{down}{rvon}{blk} mord!!! "
24016 print"{down}{down}fuer deine ruchlose tat bekommst du auch"
24017 print"noch 7000 $ und einen neuen pass!"
24020 ka(sp)=ka(sp)+7000:ag(sp)=ag(sp)or1:tp(sp)=0:x6%(sp)=1:goto1100
25000 pokera,9:pokera+1,9:print"{clr}{down}{rvon}{blk} "sp$(sp)":{$a0}job als ":print
25010 onjo(sp)goto25015,25100,25015,25200
25015 print"{rvon} rausschmeisser "
25020 print"{down}du wartest auf ein zeichen deines chefs,"
25021 print"einen ungebetenen gast zu entfernen...":fort=1to2000:next
25025 ifint(rnd(1)*2)=0thenprint"{down}heute gab es nichts!":gosub1100:goto25550
25030 print"{down}ein bursche faengt an, zu randalieren!":print"{down}tu etwas!":gosub1100
25035 gz(0)=1:kf$="kp":onint(rnd(1)*3)goto25041,25042
25040 bn$(0)="wurstfinger-fred":w=0:e=30:goto25045
25041 bn$(0)="affenface-alf":w=1:e=20:goto25045
25042 bn$(0)="der schlachter":w=3:e=20
25045 gosub5000:goto25500
25100 print"{rvon} croupier "
25105 print"{down}du musst als"
25106 print"{down}croupier natuerlich falschspielen."
25107 print"{down}welche tricks willst du anwenden:"
25110 print"{down}1 trickspiegel":print"{down}2 gezinkte karten"
25111 print"{down}3 bremse am roulette"
25115 getx$:ifx$<"1"orx$>"3"goto25115
25120 x=val(x$):ifint(rnd(1)*(6-x))=0goto25130
25125 p=int(rnd(1)*100*x)+300:print"{down}du machst gewinne und erhaeltst"
25126 print"{down}einen bonus von"p"$.":ka(sp)=ka(sp)+p:gosub1100:goto25550
25130 print"{down}ein kunde hat deine tricks durchschaut"
25131 print"{down}und zieht ein messer!":gosub1100
25135 bn$(0)="spieler":gz(0)=1:e=10:w=1:kf$="kp":gosub5000
25140 goto25500
25200 print"{rvon} killer "
25205 print"{down}du hast den typ auf dem foto gefunden"
25206 print"{down}und lockst ihn in eine seitenstrasse...":gosub1100
25210 bn$(0)="opfer":gz(0)=1:w=0:e=20:kf$="km":gosub5000:goto25500
25500 ifs=1goto25550
25505 pokera,9:pokera+1,9:print"{clr}{down}{blk}du hast deinen auftrag nicht"
25506 print"{down}erledigt!"
25510 x=-2:gosub1160:jo(sp)=0:goto1100
25550 jd(sp)=jd(sp)-1:ifjd(sp)<>0thenreturn
25555 pokera,9:pokera+1,9:print"{clr}{down}{blk}du hast deinen job erledigt und er-"
25556 print"{down}haeltst deinen lohn. es sind"jl(sp)"$!":ka(sp)=ka(sp)+jl(sp)
25560 x=3+3*(jo(sp)=2):gosub1160:jo(sp)=0:goto1100
26000 bn$(0)="die polizei":gz(0)=5+int(rnd(1)*ra(sp)/2)
26010 w=5-2*(ra(sp)>5):e=20+2*(ra(sp)-1)-int(rnd(1)*21):gosub5000
26015 ifs=1thenx=2:gosub1160:return
26020 pokera,6:pokera+1,6:print"{clr}{down}{gry3}du bist von der polizei gefasst worden."
26021 ifpl(sp)andint(rnd(1)*2)<>0goto26037
26022 print"{down}was nun:":print"{down}1 polizisten bestechen":print"{down}2 fluchtversuch"
26023 print"{down}3 ergeben"
26025 getx$:ifx$<"1"orx$>"3"goto26025
26030 onval(x$)goto26035,26040,26045
26035 p=500+500*ra(sp):print"{clr}{down}sie verlangen"p"$. ";
26036 gosub1110:ifx$="n"thenx=2:gosub1160:goto26045
26037 ifka(sp)<pthengosub1125:goto26045
26038 ka(sp)=ka(sp)-p:ifint(rnd(1)*5)=0goto26045
26039 print"{down}die bullen lassen dich gehen!":goto1100
26040 ifint(rnd(1)*tr(sp)/11)=0thenx=-5:gosub1160:goto26042
26041 print"{clr}{down}du bist gerade noch einmal entkommen!":x=2:gosub1160:goto1100
26042 print"{clr}{down}die polizisten schnappen dich"
26043 print"du musst dich ergeben!":gosub1100
26045 tp(sp)=0:x=2:gosub1160:gs(sp)=int(ra(sp)/2+.5)
26050 print"{clr}{down}deine gerichtsverhandlung...":ifra(sp)<5goto26075
26055 print"{down}willst du einen anwalt (j/n) ?":gosub1115:ifx$="n"goto26075
26060 input"{down}wieviel legst du an (0-10000$)";x$:x=val(x$):ifx=0goto26075
26061 ifx>ka(sp)orx<0thenprint"{up}{up}";:goto26060
26062 ka(sp)=ka(sp)-x:y=int(rnd(1)*(x/1000+1))+1
26065 gs(sp)=gs(sp)-y:ifgs(sp)<0thengs(sp)=0
26070 ifgs(sp)=0thenprint"{down}du wirst freigesprochen!":ms=0:goto1100
26071 print"{down}dein anwalt boxt eine strafe von"
26072 print"nur"gs(sp)"monat(en) heraus.":goto26080
26075 print"{down}du wanderst fuer"gs(sp)"monat(e) hinter":print"{down}gitter!"
26080 ms=0:jo(sp)=0:x=-10:gosub1160:po(sp)=911:goto1100
27000 ifsz=1thenprint"{clr}{down}bei solo-spiel nicht moeglich!":goto1100
27001 ifja<1925+4/12thenprint"{clr}{down}erst ab 4/1925 moeglich!":goto1100
27005 print"{clr}{down}{down}{down}{down}"tab(13)"{wht}bandenkrieg!"
27006 fori=1to20:pokera,0:fort=1to50:next:pokera,2:next
27007 print"{down}{down}{down} mit wem willst du dich anlegen:"
27010 fori=1tosz:ifi=spgoto27013
27011 print"{down} {rvon}"mid$(str$(i),2)"{rvof} "bn$(i)" "ka(i)"$  ";
27012 printleft$("{rvon}AAAAAAAAAA",1+gz(i))
27013 nexti
27015 getx$:ifx$=""goto27015
27016 us=val(x$):ifus=0thenreturn
27017 ifus<1orus>szorus=spgoto27015
27018 ifgs(us)goto27100
27020 ks(1)=us:ks(2)=sp:kf$="ks":gosub30000:a=ks(s):b=ks(1-(s=1))
27025 p=int(rnd(1)*ka(b)/6)+int(ka(b)/4)
27026 pokera,2:print"{clr}{down}{wht}"sp$(a)", du findest beim pluendern"
27027 print"{down}deines gegners"p"$. ausserdem er-"
27028 print"{down}beutest du den alkoholvorrat.":iftm(b)=0goto27035
27030 print"{down}willst du auch noch den "tm$(tm(b))
27031 print"{down}mitnehmen (j/n) ?":gosub1115:ifx$="j"thentm(a)=tm(b):tm(b)=0
27035 ka(a)=ka(a)+p:ka(b)=ka(b)-p:ag(a)=ag(a)or(ag(b)and1):ag(b)=ag(b)and254
27040 x=tk(tm(a))-ta(a):ifx>ta(b)thenx=ta(b)
27041 ta(a)=ta(a)+x:ta(b)=ta(b)-x:x=3:gosub1160:y=sp:sp=b:x=-1:gosub1160:sp=y
27045 ms=ms-10:goto1100
27100 print"{clr}{down}"sp$(us)" sitzt im knast. ein mit-"
27105 print"{down}gefangener will ihn fuer dich 'auf-"
27110 print"{down}mischen'. er verlangt 3000 $. ";:gosub1110:ifx$="n"thenreturn
27115 ifka(sp)<3000goto1125
27120 ka(sp)=ka(sp)-3000:print"{down}verteidige dich, "sp$(us)"!":gosub1100
27125 bn$(0)="mr.bonebreaker":gz(0)=1:gw(0,1)=3:ec(1)=50:ks(2)=0
27130 z1=gw(us,1):z2=gz(us):gw(us,1)=0:gz(us)=1:ks(1)=us:kf$="kg":gosub30000
27135 gw(us,1)=z1:gz(us)=z2:ifs=2goto27146
27140 x=int(rnd(1)*2)+1:print"{clr}{down}"sp$(us)"! deine strafe wird wegen"
27141 print"{down}dieser schlaegerei um"x"monat(e)":print"{down}verlaengert!"
27145 gs(us)=gs(us)+x:goto27150
27146 a=sp:b=1:gosub1350:en=0:gosub1365
27150 ms=ms-10:x=2:gosub1160:goto1100
30000 sysbl,kf$:fori=1to2:forj=1togz(ks(i)):kp(i,j)=129-18*(i=2)+p(j)
30010 pokebr+kp(i,j),193:pokefr+kp(i,j),2-4*(i=2):nextj
30015 poke211,-20*(i=2):poke214,16:syscs:printbn$(ks(i)):next
30020 ifks(2)=0thenfori=1togz(0):ri(i)=-1:next
30025 poke211,0:poke214,14:syscs:print"{rvon}der kampf beginnt..."
30030 fort=1to2000:next:gosub1450
30100 s=1:f=0:v(1)=0:v(2)=0
30105 f=f+1
30106 fori=1togz(ks(1-(s=1))):ifkp(1-(s=1),i)<0thennexti:goto30500
30107 iff<>gz(ks(s))+1goto30109
30108 s=1-(s=1):f=1:x=-20*(s=1):poke214,20:syscs:gosub1400
30109 ifkp(s,f)<0goto30105
30110 ifks(s)=0thengosub30400:goto30105
30115 x=-20*(s=2):poke211,x:poke214,18:syscs:print"{rvon}spieler   {left}{left}{left}"f
30116 print:gosub1400:a=ks(s):b=f:poke199,1:gosub1300
30120 sysi1,br+kp(s,f),fr+kp(s,f),193,2-4*(s=2),32,11
30125 getx$:ifx$=""goto30125
30130 x=0:ifx$=":"thenx=-1:goto30140
30131 ifx$=";"thenx=1:goto30140
30132 ifx$="@"thenx=-40:goto30140
30133 ifx$="/"thenx=40:goto30140
30134 ifx$=chr$(13)thensysie:gosub30200:goto30105
30135 ifx$=" "thengoto30155
30136 ifx$="q"thensysie:s=1-(s=1):goto30500
30139 goto30125
30140 p=br+kp(s,f)+x
30145 if(peek(p)<>32andpeek(p)<>96)orp<brorp>br+520goto30125
30150 kp(s,f)=kp(s,f)+x
30155 sysie:pokebr+kp(s,f),193:pokefr+kp(s,f),2-4*(s=2):goto30105
30200 pokebr+kp(s,f),193:pokefr+kp(s,f),0
30205 getx$:ifx$=""goto30205
30206 ifx$="@"thenx=-40:goto30211
30207 ifx$="/"thenx=40:goto30211
30208 ifx$=":"thenx=-1:goto30211
30209 ifx$=";"thenx=1:goto30211
30210 goto30205
30211 pokefr+kp(s,f),2-4*(s=2)
30215 r=2:w=gw(ks(s),f):ifw>3thenr=15
30216 ifw=6orw=7thenr=20
30220 p=kp(s,f):goto30225
30224 pokebr+p,az:pokefr+p,af
30225 p=p+x:r=r-1:ifp<0orp>520orpeek(br+p)=160orpeek(br+p)=156orr=0goto30235
30226 ifpeek(br+p)=193and(peek(fr+p)and15)=2-4*(s=1)goto30240
30230 az=peek(br+p):af=peek(fr+p):pokebr+p,42:pokefr+p,0:goto30224
30235 poke214,14:syscs:print"{rvon}verfehlt!":fort=1to1000:next:goto1450
30240 fori=1to1-2*(w=4)
30241 pokebr+p,170:pokefr+p,0:sysso,ws(w):pokebr+p,193:pokefr+p,2-4*(s=1)
30243 nexti
30245 ifks(s)=0thenbt=30:kr=30:goto30247
30246 a=ks(s):b=f:gosub1350
30247 ifint(rnd(1)*ts(w))=0orint(rnd(1)*(kr/10+1))=0goto30235
30250 x=1-(s=1):fori=1togz(ks(x)):ifkp(x,i)<>pthennext
30255 y=int(rnd(1)*tg(w)+bt/10)+1:ifks(x)=0goto30275
30260 a=ks(x):b=i:gosub1350:en=en-y:ifen<0thenen=0
30265 gosub1365:ifen=0goto30300
30266 goto30350
30275 ec(i)=ec(i)-y:ifec(i)<0thenec(i)=0
30280 ifec(i)=0goto30300
30281 goto30350
30300 poke214,14:syscs:ifks(x)=0thenprint"{rvon}der gegner ist tot!":goto30310
30305 print"{rvon}treffer! spieler"i"ist am ende!"
30310 fort=1to2000:next:gosub1450:kp(x,i)=-1:pokebr+p,32:v(x)=v(x)+1:return
30350 poke214,14:syscs:print"{rvon}treffer! energie -"mid$(str$(y),2)"!"
30355 fort=1to2000:next:gosub1450:return
30400 poke211,20:poke214,18:syscs:print"{rvon}spieler"f
30405 y=int(kp(s,f)/40):syscr,kp(s,f)-40*y,y:x=peek(ua)-1:y=peek(ua+1)-40
30410 ifpeek(ua+2)=1orpeek(ua+3)=1goto30420
30415 ifint(rnd(1)*2)=0orgw(ks(s),f)<4goto30450
30420 ifx=0thenx=y:goto30215
30421 ify<>0goto30450
30425 goto30215
30450 ifx<>0andri(f)<>(1+2*(x=1))thenp=x:gosub30490:ifp=0thenreturn
30451 ify<>0andri(f)<>(40+80*(y=1))thenp=y:gosub30490:ifp=0thenreturn
30455 ifx<>0andri(f)<>(1+2*(x=1))goto30460
30456 ifri(f)<>-1thenp=1:gosub30490:ifp=0thenreturn
30457 ifri(f)<>1thenp=-1:gosub30490:ifp=0thenreturn
30458 goto30465
30460 ify<>0andri(f)<>(40+80*(y=1))goto30465
30461 ifri(f)<>-40thenp=40:gosub30490:ifp=0thenreturn
30462 ifri(f)<>40thenp=-40:gosub30490:ifp=0thenreturn
30465 return
30490 q=kp(s,f)+p:ifq<0orq>520or(peek(br+q)<>32andpeek(br+q)<>96)thenreturn
30491 pokebr+kp(s,f),32:kp(s,f)=q:pokebr+kp(s,f),193:pokefr+kp(s,f),6
30492 ri(f)=p:p=0:return
30500 pokera,0:pokera+1,0:print"{clr}{down}{gry3}{rvon} sieger: "bn$(ks(s))"!!! "
30510 print"{down}{down}verluste der spieler:"
30515 print"{down}"bn$(ks(1))":"v(1):print"{down}"bn$(ks(2))":"v(2)
30520 print:goto1100
31000 tp(sp)=0:pokera,8:pokera+1,8:ifint(rnd(1)*5)=0goto31050
31005 p=int(rnd(1)*9500)+5500:print"{clr}{down}{blk}dein kapital hat im waffenhandel ge-"
31010 print"{down}arbeitet. du erhaelts"p"$ zurueck!":ka(sp)=ka(sp)+p:goto1100
31050 print"{clr}{down}{blk}die waffenhaendler, denen du 5000 $ an-"
31051 print"{down}vertraut hast, wurden geschnappt!":goto1100
40000 rem ende
40100 gosub4500:pokera,1:pokera+1,1:g=1:g(g)=1
40105 fori=2tosz:ifgf(i)>gf(g(g))theng=1:g(g)=i:goto40110
40106 ifgf(i)=gf(g(g))theng=g+1:g(g)=i
40110 nexti:ifg>1goto40150
40115 print"{clr}{down}{down}{down}{down}{down}{down}{down}{down}{down}{down}{blu}"sp$(g(g))" hat gewonnen!"
40116 print"{down}du warst von allen der brutalste,"
40117 print"{down}gemeinste und schlaueste!":goto40165
40150 print"{clr}{down}{down}{down}{down}{down}{down}{down}{down}{blu}diesmal haben mehrere die gleichen":print"{down}punkte:"
40155 fori=1tog:print"{down}"sp$(g(i)):next
40160 print"{down}alle waren gleich fiese gangster!"
40165 poke198,0:wait198,1
40166 poke198,0:wait198,1:run
50005 data slw,pub,waf,aut,kdh,sph,sgl,sub,bhf,ban,pol,ble
50100 data "haende",0,2,2,1,"messer",50,3,5,0,"knueppel",100,4,3,1
50105 data "schlagkette",500,4,4,4,"wurfsterne",3000,2,7,0
50110 data "revolver",4000,5,10,2,"gewehr",4500,5,12,2
50115 data "maschinenpistole",8000,6,15,10,"handgranaten",10000,7,18,3
50200 data "anfaenger","schlaeger","kleiner fisch","langfinger","ganove"
50205 data "mafiosi","bullenschreck","meuchelmoerder","gangsterboss"
50210 data "chef der unterwelt"
50300 data "fuesse",50,25,"talbot 90",100,35,"chevy roadster",120,40
50305 data "buick century",200,40,"auburn mod.120",150,60,"citroen t.a.",100,35
50400 data 122,81,161,120,42,202,40,200,1,241
50500 data "mies","ganz gut","todsicher","laecherlich","maessig","schlimm!"
50505 data "brutal","erschreckend!"
50600 data "papiere","falschgeld"
50700 data "alle rein!","nur neue...","per abfrage"
