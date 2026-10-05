10 d=11.1:v=peek(45)+256*peek(46):x$="0123456789abcdef":open 1,8,1,"out,s,w"
20 o$="":for j=0 to 4:q=peek(v+2+j):o$=o$+mid$(x$,int(q/16)+1,1)+mid$(x$,(q and 15)+1,1):next:print#1,"11.1:"o$
30 p=-1:for k=0 to 10000:g=k/100:r=int(g/11.1)+1:if r<>p then print#1,mid$(str$(k),2)":"mid$(str$(r),2):p=r
40 next:print#1,"end":close 1:end
