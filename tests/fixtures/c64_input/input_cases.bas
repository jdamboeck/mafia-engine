10 open 1,8,1,"out,s,w"
20 read a$:if a$="end" goto 100
30 gosub 900:print#1,"["a$"]:"str$(y)
40 goto 20
100 sp=1:dim ka(1),ta(1):a$="-5"
110 ka(sp)=1000:ta(sp)=0:x=50:p=7:gosub 900
120 ify>xthenprint#1,"buy:asked again":goto 200
130 ify=0thenprint#1,"buy:return":goto 200
140 ifka(sp)<y*pthenprint#1,"buy:broke":goto 200
150 ta(sp)=ta(sp)+y:ka(sp)=ka(sp)-p*y:print#1,"buy:"str$(ka(sp))str$(ta(sp))
200 ka(sp)=1000:ta(sp)=0:x=20:gosub 900
210 ify>ta(sp)thenprint#1,"sell:asked again":goto 300
220 ify=0thenprint#1,"sell:return":goto 300
230 ka(sp)=ka(sp)+y*x:ta(sp)=ta(sp)-y:print#1,"sell:"str$(ka(sp))str$(ta(sp))
300 print#1,"end":close 1:end
900 for i=1 to len(a$):poke 630+i,asc(mid$(a$,i,1)):next:poke 631+len(a$),13:poke 198,len(a$)+1
910 input y:return
1000 data "-3","-0","- 3","-3.5","-99999","3"," -3","+3","-5","0","-99999999","end"
