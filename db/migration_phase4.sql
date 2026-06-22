-- Phase 4 migration: insider verification fields
-- Run this in the Supabase SQL editor once.

ALTER TABLE insiders
  ADD COLUMN IF NOT EXISTS insider_verified BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS role_category TEXT DEFAULT 'other';

-- Backfill role_category for existing rows based on their role text
UPDATE insiders SET role_category = CASE
  WHEN role ILIKE '%amministratore delegato%' OR role ILIKE '%chief executive%'
    OR role ILIKE '%direttore generale%' OR role ILIKE '%general manager%'
    OR role ILIKE '%cfo%' OR role ILIKE '%chief financial%'
    OR role ILIKE '%direttore finanziario%' OR role ILIKE '%vice president%'
    THEN 'executive'
  WHEN role ILIKE '%presidente%' OR role ILIKE '%chairman%'
    OR role ILIKE '%consigliere di amministrazione%' OR role ILIKE '%board member%'
    OR role ILIKE '%membro del consiglio%' OR role ILIKE '%sindaco%'
    OR role ILIKE '%independent director%' OR role ILIKE '%non-executive%'
    THEN 'board'
  WHEN role ILIKE '%azionista rilevante%' OR role ILIKE '%major shareholder%'
    OR role ILIKE '%socio rilevante%' OR role ILIKE '%shareholder%'
    THEN 'major_shareholder'
  WHEN role ILIKE '%strettamente legata%' OR role ILIKE '%closely associated%'
    OR role ILIKE '%persona strettamente%' OR role ILIKE '%coniuge%'
    THEN 'related_person'
  ELSE 'other'
END
WHERE role_category IS NULL OR role_category = 'other';

-- Mark entity names as unverified
UPDATE insiders SET insider_verified = FALSE
WHERE
  full_name ~* '\mS\.P\.A\.\M'
  OR full_name ~* '\mS\.R\.L\.\M'
  OR full_name ILIKE '%limited%'
  OR full_name ILIKE '%holding%'
  OR full_name ILIKE '%group%'
  OR full_name ILIKE '%fund%'
  OR full_name ILIKE '%trust%'
  OR full_name ILIKE '%management%'
  OR full_name ILIKE '%partners%'
  OR full_name ILIKE '%capital%'
  OR full_name ILIKE '%investments%'
  OR full_name ILIKE '%fiduciari%';
