% jobletter.cls: a one-page cover letter class. Compile with xelatex (it needs fontspec).
% Original work for job-search-loop. Fonts: Lato and Raleway, SIL OFL 1.1, installed in the check image under
% \jobletterfontpath (see docs/templates.md).
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{jobletter}[2026/10/19 one-page cover letter]
\LoadClass[11pt,a4paper]{article}

\RequirePackage[margin=2.3cm]{geometry}
\RequirePackage{fontspec}
\RequirePackage{xcolor}
\RequirePackage{enumitem}
\RequirePackage[colorlinks=false,pdfborder={0 0 0},pdfpagemode=UseNone]{hyperref}

\definecolor{jlbody}{HTML}{2B2B2B}
\definecolor{jlmuted}{HTML}{6A6A6A}

\providecommand*{\jobletterfontpath}{/usr/local/share/fonts/jobloop/}
\setmainfont{Lato-Light}[Path=\jobletterfontpath,Extension=.ttf,
  UprightFont=Lato-Light,BoldFont=Lato-Bold,ItalicFont=Lato-Italic,BoldItalicFont=Lato-Bold]
\newfontfamily\jlbodyfont{Raleway-Medium}[Path=\jobletterfontpath,Extension=.ttf,
  UprightFont=Raleway-Medium,BoldFont=Raleway-Bold,ItalicFont=Raleway-Medium,ItalicFeatures={FakeSlant=0.2},
  BoldItalicFont=Raleway-Bold,BoldItalicFeatures={FakeSlant=0.2}]
\newfontfamily\jlnamefont{Lato-Light}[Path=\jobletterfontpath,Extension=.ttf]

\color{jlbody}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}

% \jlname{Full Name}  \jlcontact{address, phone, email}  \jlreference{any label and id, or leave empty}
\newcommand*{\jlname}[1]{\gdef\jl@name{#1}}
\newcommand*{\jlcontact}[1]{\gdef\jl@contact{#1}}
\newcommand*{\jlreference}[1]{\gdef\jl@reference{#1}}
\gdef\jl@name{}\gdef\jl@contact{}\gdef\jl@reference{}

% The centred name and contact line; no rule.
\newcommand*{\makeletterhead}{%
  \begin{center}
    {\jlnamefont\fontsize{40}{44}\selectfont\jl@name\par}
    \vspace{6pt}
    {\jlbodyfont\color{jlmuted}\fontsize{11}{14}\selectfont\jl@contact\par}
  \end{center}\vspace{14pt}}

% Date and contact lines use the body font: Lato's hyphen is stored as U+2010 and would corrupt emails, phone
% numbers and reference ids in the text layer.
% Date, optionally followed by the posting's reference id (label chosen by the drafter, so any language works).
\newcommand*{\letterdate}[1]{\begin{flushright}\jlbodyfont\color{jlmuted}#1%
  \ifx\jl@reference\@empty\else\\\jl@reference\fi\end{flushright}\vspace{10pt}}

\newcommand*{\salutation}[1]{{\jlbodyfont\fontsize{11}{13}\selectfont #1\par}\vspace{9pt}}

% One body paragraph. Bullet lists go in the letterbullets environment, not inside \lettercontent.
\newcommand*{\lettercontent}[1]{{\jlbodyfont\fontsize{11}{13}\selectfont #1\par}\vspace{9pt}}

\newenvironment{letterbullets}
  {\begingroup\jlbodyfont\fontsize{11}{13}\selectfont
   \begin{itemize}[leftmargin=1.4em,itemsep=2pt,topsep=0pt,parsep=0pt]}
  {\end{itemize}\endgroup\vspace{9pt}}

% \closingline{one sentence}   \signoff{Yours sincerely,}{Full Name}
\newcommand*{\closingline}[1]{\lettercontent{#1}}
\newcommand*{\signoff}[2]{{\jlbodyfont\fontsize{11}{13}\selectfont #1\par\vspace{16pt}#2\par}}
