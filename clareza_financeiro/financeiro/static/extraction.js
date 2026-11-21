/* Envia o documento para extração, mostra avisos e preenche somente campos vazios; guarda o token para anexar o arquivo ao salvar a despesa. */
document.addEventListener('DOMContentLoaded',()=>{
  const form=document.querySelector('#expense-form[data-extraction-url]');
  if(!form)return;
  const file=form.querySelector('#id_document');
  const token=form.querySelector('#id_extraction_token');
  const button=form.querySelector('#extract-button');
  const status=form.querySelector('#extraction-status');
  const warnings=form.querySelector('#extraction-warnings');
  const staged=form.querySelector('#staged-file');
  const originalLabel=button.textContent;

  const setWarnings=(items=[])=>{
    warnings.replaceChildren(...items.map(item=>{const li=document.createElement('li');li.textContent=item;return li;}));
    warnings.hidden=!items.length;
  };
  const stageFile=()=>{
    file.value='';
    button.disabled=true;
    staged.hidden=false;
  };
  const markAutomatic=(input)=>{
    const label=input.closest('label');
    label.classList.add('auto-filled');
    if(!label.querySelector('.auto-badge')){
      const badge=document.createElement('span');
      badge.className='auto-badge';
      badge.textContent='Preenchido automaticamente';
      input.insertAdjacentElement('afterend',badge);
    }
  };
  const applySuggestions=(fields)=>{
    let filled=0,preserved=0;
    Object.entries(fields||{}).forEach(([name,value])=>{
      const input=form.elements.namedItem(name);
      if(!input||!value)return;
      if(String(input.value).trim()){preserved++;return;}
      input.value=value;
      markAutomatic(input);
      filled++;
    });
    return {filled,preserved};
  };

  const tokenHasError=token.nextElementSibling?.classList.contains('errorlist');
  if(tokenHasError)token.value='';
  else if(token.value){stageFile();status.textContent='O arquivo extraído continua pronto para ser salvo.';}
  file.addEventListener('change',()=>{
    token.value='';
    staged.hidden=true;
    const tooLarge=file.files[0]?.size>10*1024*1024;
    button.disabled=!file.files.length||tooLarge;
    status.textContent=tooLarge?'O arquivo deve ter até 10 MB.':'';
    status.className=tooLarge?'extraction-status error':'extraction-status';
    setWarnings();
  });
  form.querySelectorAll('input,select,textarea').forEach(input=>input.addEventListener('input',()=>{
    const label=input.closest('label');
    if(!label||!label.classList.contains('auto-filled'))return;
    label.classList.remove('auto-filled');
    label.querySelector('.auto-badge')?.remove();
  }));

  button.addEventListener('click',async()=>{
    if(!file.files.length||button.disabled)return;
    button.disabled=true;
    button.setAttribute('aria-busy','true');
    button.textContent='Processando…';
    status.className='extraction-status processing';
    status.textContent='Lendo o documento localmente. Isso pode levar alguns segundos.';
    setWarnings();
    const body=new FormData();
    body.append('document',file.files[0]);
    try{
      const response=await fetch(form.dataset.extractionUrl,{
        method:'POST',body,credentials:'same-origin',headers:{'X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value},
      });
      let data={};
      try{data=await response.json();}catch{}
      if(data.token){token.value=data.token;stageFile();}
      if(!response.ok)throw new Error(data.error||'Não foi possível extrair informações do arquivo.');
      if(data.error){
        status.className='extraction-status error';
        status.textContent=data.error;
        return;
      }
      const counts=applySuggestions(data.fields);
      status.className='extraction-status success';
      status.textContent=data.message;
      if(counts.preserved)status.textContent+=` ${counts.preserved} campo(s) já preenchido(s) foram preservados.`;
      setWarnings(data.warnings);
    }catch(error){
      status.className='extraction-status error';
      status.textContent=error.message||'Falha ao processar o arquivo. Você pode continuar preenchendo manualmente.';
    }finally{
      button.removeAttribute('aria-busy');
      button.textContent=originalLabel;
      if(!token.value)button.disabled=!file.files.length;
    }
  });
});
