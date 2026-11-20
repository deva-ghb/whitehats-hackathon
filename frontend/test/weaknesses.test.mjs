import assert from 'node:assert/strict'
import { test } from 'node:test'
import { IMAGE_ROWS, capImages, imageName } from '../src/studio/weaknesses.js'

const row = (advisory, image) => ({ advisory, package: 'p', image })

test('every package row is listed and each image only its worst few, saying how many more', () => {
  const image = 'public.ecr.aws/lambda/python:3.9@sha256:abc'
  const rows = [row('PYSEC-1'), ...Array.from({ length: IMAGE_ROWS + 3 }, (_, n) => row(`CVE-${n}`, image)), row('PYSEC-2')]
  const { shown, more } = capImages(rows)
  assert.equal(shown.length, IMAGE_ROWS + 2)
  assert.deepEqual(shown.filter((item) => !item.image).map((item) => item.advisory), ['PYSEC-1', 'PYSEC-2'])
  assert.deepEqual(more, [[image, 3]])
  assert.deepEqual(capImages([row('CVE-1', image)]).more, [])
})

test('an image is named by name and tag, without its digest', () => {
  assert.equal(imageName('public.ecr.aws/lambda/python:3.9@sha256:abc'), 'public.ecr.aws/lambda/python:3.9')
  assert.equal(imageName('python:3.12'), 'python:3.12')
})
