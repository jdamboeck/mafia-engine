10 g=0:h=0:t=0:u=0:v=peek(45)+256*peek(46):x$="0123456789abcdef":x8=.7
20 open 1,8,1,"out,s,w"
30 for i=1 to 12:g=g+(3*x8):next:for i=1 to 4:h=h+(9*x8):next
40 t=int(g*100)/100:u=int(h*100)/100
50 for k=0 to 3:o$="":for j=0 to 4:q=peek(v+2+7*k+j):o$=o$+mid$(x$,int(q/16)+1,1)+mid$(x$,(q and 15)+1,1):next:print#1,o$:next
60 print#1,str$(g)str$(h)str$(t)str$(u):print#1,"end":close 1:end
