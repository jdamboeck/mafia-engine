10 open 1,8,1,"out,s,w"
20 read p:if p=99999 goto 200
30 print"{clr}{down}{gry3}du hast deine miete nicht puenktlich"
40 print"{down}gezahlt. man hat dir moebel im wert von"
50 print"{down}{left}"p"$ gepfaendet!"
60 print#1,"p="p
70 for r=0 to 7:l$="":for c=0 to 39:s=peek(1024+40*r+c)
80 if s<32 then s=s+64
90 l$=l$+chr$(s):next c
100 print#1,"/"l$"/":next r
110 goto 20
120 data 250,3000,5,-50,99999
200 close 1
