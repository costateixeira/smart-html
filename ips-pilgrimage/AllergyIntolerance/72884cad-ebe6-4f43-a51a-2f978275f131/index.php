<?php
function Redirect($url)
{
  header('Location: ' . $url, true, 302);
  exit();
}

$accept = $_SERVER['HTTP_ACCEPT'];
if (strpos($accept, 'application/json+fhir') !== false)
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/AllergyIntolerance-72884cad-ebe6-4f43-a51a-2f978275f131.json2');
elseif (strpos($accept, 'application/fhir+json') !== false)
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/AllergyIntolerance-72884cad-ebe6-4f43-a51a-2f978275f131.json1');
elseif (strpos($accept, 'json') !== false)
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/AllergyIntolerance-72884cad-ebe6-4f43-a51a-2f978275f131.json');
elseif (strpos($accept, 'application/xml+fhir') !== false)
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/AllergyIntolerance-72884cad-ebe6-4f43-a51a-2f978275f131.xml2');
elseif (strpos($accept, 'application/fhir+xml') !== false)
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/AllergyIntolerance-72884cad-ebe6-4f43-a51a-2f978275f131.xml1');
elseif (strpos($accept, 'html') !== false)
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/AllergyIntolerance-72884cad-ebe6-4f43-a51a-2f978275f131.html');
else 
  Redirect('http://smart.who.int/ips-pilgrimage/2.0.2/AllergyIntolerance-72884cad-ebe6-4f43-a51a-2f978275f131.xml');
?>
    
You should not be seeing this page. If you do, PHP has failed badly.
