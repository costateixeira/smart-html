<?php
function Redirect($url)
{
  header('Location: ' . $url, true, 302);
  exit();
}

$accept = $_SERVER['HTTP_ACCEPT'];
if (strpos($accept, 'application/json+fhir') !== false)
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/ValueSet-IPS.HAJJ.ACTOR.SYSTEM.json2');
elseif (strpos($accept, 'application/fhir+json') !== false)
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/ValueSet-IPS.HAJJ.ACTOR.SYSTEM.json1');
elseif (strpos($accept, 'json') !== false)
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/ValueSet-IPS.HAJJ.ACTOR.SYSTEM.json');
elseif (strpos($accept, 'application/xml+fhir') !== false)
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/ValueSet-IPS.HAJJ.ACTOR.SYSTEM.xml2');
elseif (strpos($accept, 'application/fhir+xml') !== false)
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/ValueSet-IPS.HAJJ.ACTOR.SYSTEM.xml1');
elseif (strpos($accept, 'html') !== false)
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/ValueSet-IPS.HAJJ.ACTOR.SYSTEM.html');
else 
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/ValueSet-IPS.HAJJ.ACTOR.SYSTEM.xml');
?>
    
You should not be seeing this page. If you do, PHP has failed badly.
